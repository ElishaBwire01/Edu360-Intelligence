from django.test import RequestFactory
from django.contrib.auth.models import User

from core.views import admin_dashboard

def main():
    factory = RequestFactory()
    request = factory.get('/admin-dashboard/')
    request.user = User.objects.filter(is_superuser=True).first()
    if request.user is None:
        print("No superuser exists; create one before running this diagnostic.")
        return

    response = admin_dashboard(request)
    if hasattr(response, 'context_data'):
        print("=" * 50)
        print("CONTEXT DATA:")
        print("=" * 50)
        for key, value in response.context_data.items():
            if key in ['streams', 'forms']:
                print(f"{key}: {value}")
        print("=" * 50)
    else:
        print("No context data available")


if __name__ == "__main__":
    main()
