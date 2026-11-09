import json
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from sources.models import NewsSource
from newsroom.models import Article, Draft


class AutomationTests(TestCase):
    def setUp(self):
        self.token = secrets.token_urlsafe(48)
        self.settings_override = override_settings(COLLECTOR_TOKEN=self.token)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client = Client(enforce_csrf_checks=True)
        self.headers = {'HTTP_AUTHORIZATION': 'Bearer ' + self.token}
        self.source = NewsSource.objects.create(name='Example', rss_url='https://example.com/feed')
        self.article = {'title': 'Verified title', 'url': 'https://example.com/article/1', 'body': 'A fact reported by the source.', 'published_at': None}

    def send(self, articles=None, **extra):
        payload = {'source_id': self.source.pk, 'source_url': self.source.rss_url, 'articles': articles if articles is not None else [self.article], **extra}
        return self.client.post('/collector/results/', json.dumps(payload), content_type='application/json', **self.headers)

    def test_machine_auth_is_required_even_for_staff(self):
        staff = get_user_model().objects.create_user(username='staff-auto', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/collector/queue/').status_code, 401)
        self.assertEqual(self.client.post('/collector/results/', '{}', content_type='application/json').status_code, 401)
        self.assertFalse(Article.objects.exists())

    @override_settings(COLLECTOR_TOKEN='')
    def test_unconfigured_endpoints_are_disabled(self):
        self.assertEqual(self.client.get('/collector/queue/').status_code, 503)

    def test_queue_excludes_inactive_and_recent_but_accepts_new(self):
        inactive = NewsSource.objects.create(name='Off', rss_url='https://example.com/off', is_active=False)
        response = self.client.get('/collector/queue/', **self.headers)
        self.assertEqual([s['id'] for s in response.json()['sources']], [self.source.pk])
        self.source.collection_requested = False
        self.source.last_checked_at = timezone.now()
        self.source.save()
        self.assertEqual(self.client.get('/collector/queue/', **self.headers).json()['sources'], [])
        self.source.last_checked_at = timezone.now() - timedelta(hours=2)
        self.source.save()
        self.assertEqual(len(self.client.get('/collector/queue/', **self.headers).json()['sources']), 1)

    def test_ingestion_creates_article_and_three_unapproved_drafts_once(self):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['drafts'], 3)
        self.assertEqual(Draft.objects.count(), 3)
        self.assertEqual(set(Draft.objects.values_list('channel', flat=True)), {'social', 'messenger', 'email'})
        for draft in Draft.objects.all():
            self.assertEqual(draft.status, 'draft')
            self.assertEqual(draft.generation_method, 'factual_template')
            self.assertIsNone(draft.approved_by)
            self.assertIn(self.article['url'], draft.body)
            self.assertTrue(draft.revisions.get().automated)
        self.assertIsNone(Article.objects.get().published_at)
        response = self.send()
        self.assertEqual(response.json()['drafts'], 0)
        self.assertEqual(Article.objects.count(), 1)
        self.assertEqual(Draft.objects.count(), 3)

    def test_manual_text_is_not_overwritten(self):
        article = Article.objects.create(title='Old', url=self.article['url'])
        Draft.objects.create(article=article, channel='social', body='Human text')
        self.send()
        self.assertEqual(Draft.objects.get(channel='social').body, 'Human text')
        self.assertEqual(Draft.objects.count(), 3)

    def test_keywords_filter_before_drafts_are_created(self):
        self.source.keywords = 'marketing'
        self.source.save()
        self.assertEqual(self.send().json()['filtered'], 1)
        self.assertFalse(Draft.objects.exists())

    def test_errors_are_visible_without_articles(self):
        self.assertEqual(self.send([], error='robots.txt forbids this path').status_code, 200)
        self.source.refresh_from_db()
        self.assertIn('robots.txt', self.source.collection_error)
        self.assertIsNotNone(self.source.last_checked_at)
        self.assertFalse(Article.objects.exists())

    def test_invalid_payloads_are_rejected_without_partial_writes(self):
        for rows in ([self.article, {'title': 'Invalid', 'url': 'javascript:alert(1)'}], [dict(self.article, title='x'*301)], [dict(self.article, body=['not a string'])]):
            response = self.send(rows)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(Article.objects.exists())
        self.assertEqual(self.send(source_url='https://changed.example/').status_code, 409)

    def test_disabled_source_cannot_receive_results(self):
        self.source.is_active = False
        self.source.save()
        self.assertEqual(self.send().status_code, 404)
        self.assertFalse(Draft.objects.exists())

    def test_retry_and_edit_are_staff_only(self):
        for name in ('edit', 'retry'):
            url = reverse('sources:' + name, args=[self.source.pk])
            response = Client().post(url)
            self.assertEqual(response.status_code, 302)
        staff = get_user_model().objects.create_user(username='editor-auto', is_staff=True)
        client = Client()
        client.force_login(staff)
        self.assertEqual(client.get(reverse('sources:retry', args=[self.source.pk])).status_code, 405)
        self.source.collection_requested = False
        self.source.save()
        client.post(reverse('sources:retry', args=[self.source.pk]))
        self.source.refresh_from_db()
        self.assertTrue(self.source.collection_requested)

    def test_ai_drafts_are_labelled_and_never_autoapproved(self):
        drafts = [{'channel': channel, 'subject': 'AI subject', 'body': 'AI text to verify'} for channel in ('social', 'messenger', 'email')]
        response = self.send([dict(self.article, drafts=drafts)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Draft.objects.filter(generation_method='ai', status='draft').count(), 3)
        self.assertIn(self.article['url'], Draft.objects.first().body)

    def test_incomplete_ai_payload_is_not_silently_accepted(self):
        response = self.send([dict(self.article, drafts=[{'channel': 'email', 'subject': '', 'body': 'Incomplete'}])])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Article.objects.exists())

    def test_ai_error_keeps_article_and_allows_future_generation_retry(self):
        response = self.send([dict(self.article, writing_error='Provider unavailable')])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Article.objects.count(), 1)
        self.assertFalse(Draft.objects.exists())
        self.source.refresh_from_db()
        self.assertIn('ИИ', self.source.collection_error)
        self.assertFalse(Article.objects.get().auto_prepared)
        response = self.send()
        self.assertEqual(response.json()['drafts'], 3)
        self.assertEqual(Article.objects.count(), 1)
