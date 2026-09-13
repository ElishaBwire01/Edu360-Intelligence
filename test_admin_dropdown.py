#!/usr/bin/env python
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'disciplinary_program.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from core.models import GradeLevel

User = get_user_model()

def main():
    client = Client()
    user = User.objects.filter(is_superuser=True).first()
    if user is None:
        print("No superuser exists; create one before running this diagnostic.")
        return
    client.force_login(user)

    response = client.get('/admin-dashboard/')
    content = response.content.decode('utf-8')

    print("Checking admin dashboard for grade options...")
    print("-" * 50)

    grades = GradeLevel.objects.filter(is_active=True).order_by('order')
    found = [grade.name for grade in grades if grade.name in content]
    missing = [grade.name for grade in grades if grade.name not in content]

    if found:
        print(f"Found in HTML: {', '.join(found)}")
    if missing:
        print(f"Missing from HTML: {', '.join(missing)}")

    if 'name="grade_level"' in content:
        print("grade_level dropdown found")
    else:
        print("grade_level dropdown not found")


if __name__ == "__main__":
    main()
