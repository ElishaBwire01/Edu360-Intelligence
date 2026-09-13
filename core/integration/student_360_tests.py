from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from .student_360 import Student360Service


class Student360AuthorizationTests(SimpleTestCase):
    def test_denied_student360_access_fails_before_data_assembly(self):
        student = SimpleNamespace(pk=1)

        with self.assertRaises(PermissionDenied):
            Student360Service.build(student, access_checker=lambda value: False)