import json
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from newsroom.models import ArchiveSearch, Article, Draft, GenerationRequest
from sources.models import NewsSource


@override_settings(COLLECTOR_TOKEN='test-only-' + 'x' * 40)
class ArchiveJobTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username='editor', is_staff=True)
        self.source = NewsSource.objects.create(name='Example', rss_url='https://example.com/')
        self.headers = {'HTTP_AUTHORIZATION': 'Bearer test-only-' + 'x' * 40}
        self.job = ArchiveSearch.objects.create(source=self.source, source_url=self.source.rss_url, query='банкротства')
        self.row = {'requested_url': 'https://example.com/article/1', 'url': 'https://example.com/article/1',
            'title': 'Банкротство предприятия', 'body': 'Описание', 'content_text': 'Факты о банкротстве. ' * 20,
            'content_complete': True, 'published_at': None}

    def send(self, data):
        return self.client.post('/collector/job-results/', json.dumps(data), content_type='application/json', **self.headers)

    def discover(self, urls=None):
        return self.send({'kind': 'search', 'id': self.job.pk, 'cursor': 0,
                          'urls': urls or [self.row['url']], 'limited': False})

    def test_all_editorial_routes_require_staff(self):
        article = Article.objects.create(title='Title', url=self.row['url'])
        job = GenerationRequest.objects.create(article=article, channel='social')
        routes = ['/articles/search/new/', f'/articles/search/{self.job.pk}/',
                  f'/articles/{article.pk}/generate/', f'/generation/{job.pk}/']
        ordinary = get_user_model().objects.create_user(username='visitor')
        for user in (None, ordinary, self.staff):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in routes:
                with self.subTest(url=url, user=user):
                    self.assertEqual(self.client.get(url).status_code, 200 if user == self.staff else 302)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_csrf_protects_staff_actions_but_machine_needs_its_own_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post('/articles/search/new/', {'source': self.source.pk, 'query': 'банкротства'}).status_code, 403)
        self.assertEqual(client.get('/collector/jobs/').status_code, 401)
        self.assertEqual(client.post('/collector/job-results/', '{}', content_type='application/json').status_code, 401)

    def test_progress_filtering_dedup_and_no_automatic_posts(self):
        self.assertEqual(self.discover().status_code, 200)
        self.assertEqual(self.client.get('/collector/jobs/', **self.headers).json()['search']['urls'], [self.row['url']])
        payload = {'kind': 'search', 'id': self.job.pk, 'cursor': 0, 'rows': [self.row]}
        self.assertEqual(self.send(payload).json()['status'], 'completed')
        self.assertEqual(self.send(payload).status_code, 409)
        self.job.refresh_from_db()
        self.assertEqual((self.job.cursor, self.job.matched_count), (1, 1))
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(Draft.objects.count(), 0)
        other = ArchiveSearch.objects.create(source=self.source, source_url=self.source.rss_url, query='банкротства',
                                            status='running', urls=[self.row['url']])
        self.assertEqual(self.send({**payload, 'id': other.pk}).status_code, 200)
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(other.articles.count(), 1)

    def test_errors_are_partial_and_unrelated_articles_not_saved(self):
        other_url = 'https://example.com/article/2'
        self.discover([self.row['url'], other_url])
        irrelevant = {**self.row, 'title': 'Налоги', 'content_text': 'Новость о налогах. ' * 20}
        payload = {'kind': 'search', 'id': self.job.pk, 'cursor': 0, 'rows': [irrelevant, {'requested_url': other_url, 'error': 'robots denied'}]}
        self.assertEqual(self.send(payload).json()['status'], 'partial')
        self.assertFalse(Article.objects.exists())
        self.job.refresh_from_db()
        self.assertEqual((self.job.cursor, self.job.error_count), (2, 1))

    def test_malformed_batch_and_cross_site_urls_are_rejected_before_writes(self):
        self.assertEqual(self.discover(['https://other.example/article/1']).status_code, 400)
        self.discover([self.row['url'], 'https://example.com/article/2'])
        invalid = {**self.row, 'requested_url': 'https://example.com/article/2', 'url': 'https://evil.example/a'}
        self.assertEqual(self.send({'kind': 'search', 'id': self.job.pk, 'cursor': 0, 'rows': [self.row, invalid]}).status_code, 400)
        self.assertFalse(Article.objects.exists())
        self.job.refresh_from_db()
        self.assertEqual(self.job.cursor, 0)

    def test_cancel_and_inactive_source_do_not_run(self):
        self.client.force_login(self.staff)
        self.client.post(reverse('newsroom:archive', args=[self.job.pk]))
        self.assertIsNone(self.client.get('/collector/jobs/', **self.headers).json()['search'])
        self.assertEqual(self.discover().status_code, 409)
        self.source.is_active = False; self.source.save()
        self.assertEqual(self.discover().status_code, 404)

    def test_generation_one_format_preserves_manual_drafts_and_requires_consent(self):
        article = Article.objects.create(title='Title', url=self.row['url'], body='Editor notes')
        old = Draft.objects.create(article=article, channel='social', body='Human text')
        self.client.force_login(self.staff)
        url = reverse('newsroom:generate', args=[article.pk])
        data = {'channel': 'email', 'editorial_name': 'Наша редакция'}
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assertFalse(GenerationRequest.objects.exists())
        for _ in range(2):
            self.assertEqual(self.client.post(url, {**data, 'consent': 'on'}).status_code, 302)
        job = GenerationRequest.objects.get()
        payload = {'kind': 'generation', 'id': job.pk, 'subject': 'Email тема', 'body': 'Новый текст', 'article': self.row}
        self.assertEqual(self.send(payload).json()['status'], 'completed')
        self.assertEqual(self.send(payload).status_code, 409)
        job.refresh_from_db()
        self.assertEqual(job.draft.channel, 'email')
        self.assertEqual(job.draft.generation_method, 'ai')
        self.assertEqual(job.draft.status, 'draft')
        self.assertIsNone(job.draft.approved_by)
        self.assertTrue(job.draft.revisions.get().automated)
        self.assertIn('По материалам example.com', job.draft.body)
        self.assertIn(article.url, job.draft.body)
        old.refresh_from_db(); article.refresh_from_db()
        self.assertEqual(old.body, 'Human text')
        self.assertEqual(article.body, 'Editor notes')
        self.assertEqual(Draft.objects.count(), 2)

    def test_missing_provider_is_blocked_with_no_fake_draft(self):
        article = Article.objects.create(title='Title', url=self.row['url'])
        job = GenerationRequest.objects.create(article=article, channel='messenger')
        self.assertEqual(self.send({'kind': 'generation', 'id': job.pk, 'error': 'Connect free key', 'blocked': True}).json()['status'], 'blocked')
        self.assertFalse(Draft.objects.exists())
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('newsroom:generation', args=[job.pk])), 'Connect free key')

    def test_source_keywords_start_archive_automatically_and_edit_replaces_pending(self):
        self.client.force_login(self.staff)
        data = {'name': 'Сайт', 'rss_url': 'https://example.com/', 'keywords': 'банкротства', 'is_active': 'on'}
        self.assertEqual(self.client.post(reverse('sources:list'), data).status_code, 302)
        source = NewsSource.objects.exclude(pk=self.source.pk).get()
        job = ArchiveSearch.objects.get(source=source)
        self.assertEqual(job.query, 'банкротства')
        self.client.post(reverse('sources:edit', args=[source.pk]), {**data, 'keywords': 'налоги'})
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
        self.assertTrue(ArchiveSearch.objects.filter(source=source, query='налоги', status='queued').exists())
