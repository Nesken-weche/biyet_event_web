release: python manage.py migrate --noinput
web: gunicorn biyet.wsgi --workers 2 --timeout 60 --log-file -