"""Tests for the backpopulate_program_credentials management command."""
import ddt
import logging
import mock
from certificates.models import CertificateStatuses  # pylint: disable=import-error
from course_modes.models import CourseMode
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from student.tests.factories import UserFactory

from lms.djangoapps.certificates.api import MODES
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.catalog.tests.factories import (
    generate_course_run_key,
    ProgramFactory,
    CourseFactory,
    CourseRunFactory,
)
from openedx.core.djangoapps.catalog.tests.mixins import CatalogIntegrationMixin
from openedx.core.djangoapps.credentials.tests.mixins import CredentialsApiConfigMixin
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

COMMAND_MODULE = 'openedx.core.djangoapps.programs.management.commands.backpopulate_program_credentials'
logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

@ddt.ddt
@mock.patch(COMMAND_MODULE + '.get_programs')
@mock.patch(COMMAND_MODULE + '.award_program_certificates.delay')
@skip_unless_lms
class BackpopulateProgramCredentialsTests(CatalogIntegrationMixin, CredentialsApiConfigMixin, TestCase):
    """Tests for the backpopulate_program_credentials management command."""
    course_run_key, alternate_course_run_key = (generate_course_run_key() for __ in range(2))

    def setUp(self):
        super(BackpopulateProgramCredentialsTests, self).setUp()

        Site.objects.all().delete()
        self.site = SiteFactory()

        self.alice = UserFactory()
        self.bob = UserFactory()

        # Disable certification to prevent the task from being triggered when
        # setting up test data (i.e., certificates with a passing status), thereby
        # skewing mock call counts.
        self.create_credentials_config(enable_learner_issuance=False)

        catalog_integration = self.create_catalog_integration()
        UserFactory(username=catalog_integration.service_username)

    @ddt.data(True, False)
    def test_handle(self, commit, mock_task, mock_get_programs):
        """
        Verify that relevant tasks are only enqueued when the commit option is passed.
        """
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=self.course_run_key),
                    ]),
                ]
            ),
        ]
        mock_get_programs.return_value = data

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.alternate_course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=commit)

        if commit:
            mock_task.assert_called_once_with(self.alice.username, self.site.id)
        else:
            mock_task.assert_not_called()

    def test_handle_professional(self, mock_task, mock_get_programs):
        """ Verify the task can handle both professional and no-id-professional modes. """
        mock_get_programs.return_value = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=self.course_run_key, type='professional'),
                    ]),
                ]
            ),
        ]

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=CourseMode.PROFESSIONAL,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.course_run_key,
            mode=CourseMode.NO_ID_PROFESSIONAL_MODE,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=True)

        # The task should be called for both users since professional and no-id-professional are equivalent.
        mock_task.assert_has_calls([
            mock.call(self.alice.username, self.site.id),
            mock.call(self.bob.username, self.site.id)
        ])

    @ddt.data(
        ([
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=alternate_course_run_key),
                    ]),
                ]
            ),
        ]),
        [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key),
                    ]),
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=alternate_course_run_key),
                    ]),
                ]
            ),
        ],
        [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key),
                        CourseRunFactory(key=alternate_course_run_key),
                    ]),
                ]
            ),
        ],
    )
    def test_handle_flatten(self, data, mock_task, mock_get_programs):
        """Verify that program structures are flattened correctly."""
        mock_get_programs.return_value = data

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.alternate_course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=True)

        calls = [
            mock.call(self.alice.username, self.site.id),
            mock.call(self.bob.username, self.site.id)
        ]
        mock_task.assert_has_calls(calls, any_order=True)

    def test_handle_username_dedup(self, mock_task, mock_get_programs):
        """
        Verify that only one task is enqueued for a user with multiple eligible
        course run certificates.
        """
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=self.course_run_key),
                        CourseRunFactory(key=self.alternate_course_run_key),
                    ]),
                ]
            ),
        ]
        mock_get_programs.return_value = data

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.alternate_course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=True)

        mock_task.assert_called_once_with(self.alice.username, self.site.id)

    def test_handle_mode_slugs(self, mock_task, mock_get_programs):
        """
        Verify that course run types are taken into account when identifying
        qualifying course run certificates.
        """
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=self.course_run_key, type='honor'),
                    ]),
                ]
            ),
        ]
        mock_get_programs.return_value = data

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.honor,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=True)

        mock_task.assert_called_once_with(self.alice.username, self.site.id)

    def test_handle_passing_status(self, mock_task, mock_get_programs):
        """
        Verify that only course run certificates with a passing status are selected.
        """
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=self.course_run_key),
                    ]),
                ]
            ),
        ]
        mock_get_programs.return_value = data

        passing_status = CertificateStatuses.downloadable
        failing_status = CertificateStatuses.notpassing

        self.assertIn(passing_status, CertificateStatuses.PASSED_STATUSES)
        self.assertNotIn(failing_status, CertificateStatuses.PASSED_STATUSES)

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=passing_status,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=failing_status,
        )

        call_command('backpopulate_program_credentials', commit=True)

        mock_task.assert_called_once_with(self.alice.username, self.site.id)

    @mock.patch(COMMAND_MODULE + '.logger.exception')
    def test_handle_enqueue_failure(self, mock_log, mock_task, mock_get_programs):
        """Verify that failure to enqueue a task doesn't halt execution."""

        def side_effect(username):
            """Simulate failure to enqueue a task."""
            if username == self.alice.username:
                raise Exception

        mock_task.side_effect = side_effect

        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=self.course_run_key),
                    ]),
                ]
            ),
        ]
        mock_get_programs.return_value = data

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=True)

        self.assertTrue(mock_log.called)

        calls = [
            mock.call(self.alice.username, self.site.id),
            mock.call(self.bob.username, self.site.id)
        ]
        mock_task.assert_has_calls(calls, any_order=True)

    def test_handle_with_multiple_sites(self, mock_task, mock_get_programs):
        """ Verify the command iterates over all Sites in the system. """

        Site.objects.all().delete()
        sites = [SiteFactory(domain='test1.fake'), SiteFactory(domain='test2.fake')]
        self.assertEqual(Site.objects.all().count(), 2)

        mock_get_programs.side_effect = (
            [
                ProgramFactory(
                    courses=[
                        CourseFactory(course_runs=[
                            CourseRunFactory(key=self.course_run_key),
                        ]),
                    ]
                ),
            ],
            [
                ProgramFactory(
                    courses=[
                        CourseFactory(course_runs=[
                            CourseRunFactory(key=self.alternate_course_run_key),
                        ]),
                    ]
                ),
            ],
        )

        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )
        GeneratedCertificateFactory(
            user=self.alice,
            course_id=self.alternate_course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )
        GeneratedCertificateFactory(
            user=self.bob,
            course_id=self.alternate_course_run_key,
            mode=MODES.verified,
            status=CertificateStatuses.downloadable,
        )

        call_command('backpopulate_program_credentials', commit=True)
        self.assertEqual(mock_get_programs.call_count, 2)
        calls = [
            mock.call(self.alice.username, sites[0].id),
            mock.call(self.bob.username, sites[0].id),
            mock.call(self.alice.username, sites[1].id),
            mock.call(self.bob.username, sites[1].id),
        ]
        mock_task.assert_has_calls(calls, any_order=True)
