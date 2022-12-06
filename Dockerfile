FROM python:3.6-alpine

RUN apk add build-base && pip install -r requirements.txt

WORKDIR /app
COPY . .

CMD ["python3","-u","/app/run.py"]
