FROM python:3.6-alpine

RUN apk add build-base && pip install websocket-client==1.3.1 dydx-v3-python==1.9.0

WORKDIR /app
COPY . .

CMD ["python3","-u","/app/run.py"]
