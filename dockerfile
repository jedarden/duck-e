FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

COPY ./app ./app

CMD [ "uvicorn", "app.main:app", "--port", "8000", "--host", "0.0.0.0"]