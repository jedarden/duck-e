FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

COPY ./app ./app
COPY ./VERSION ./VERSION

# Read version from file and set as environment variable
RUN export APP_VERSION=$(cat VERSION) && echo "APP_VERSION=$APP_VERSION" >> /etc/environment
ENV APP_VERSION_FILE=/app/VERSION

CMD [ "uvicorn", "app.main:app", "--port", "8000", "--host", "0.0.0.0"]