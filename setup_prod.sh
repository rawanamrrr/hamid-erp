#!/bin/bash
set -e
echo "Cleaning up old venv..."
rm -rf venv
echo "Creating virtual environment with python3.10..."
python3.10 -m venv venv
echo "Converting requirements.txt to utf-8..."
iconv -f utf-16le -t utf-8 requirements.txt > requirements_utf8.txt || true
sed -i '/pywin32/d' requirements_utf8.txt
sed -i '/waitress/d' requirements_utf8.txt
echo "Installing requirements..."
source venv/bin/activate
pip install -U pip setuptools wheel
pip install gunicorn  # Adding gunicorn for linux deployment
pip install waitress  # Re-install waitress cleanly just in case
pip install -r requirements_utf8.txt || echo "pip install had some errors, continuing..."
echo "Running migrations..."
python manage.py migrate --noinput || echo "Failed to migrate"
echo "Collecting static files..."
export DJANGO_SETTINGS_MODULE=textile_pos.production_settings
python manage.py collectstatic --noinput || echo "Failed to collect static files"
echo "Done setting up environment."
