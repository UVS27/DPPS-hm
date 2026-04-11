FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    locales \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && localedef -i ru_RU -c -f UTF-8 -A /usr/share/locale/locale.alias ru_RU.UTF-8

ENV LANG=ru_RU.utf8

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py webapp.py config_docker.json ./
COPY templates ./templates

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "webapp:app"]