FROM python:3.11-slim

WORKDIR /app

COPY app.py config.example.json ./
COPY backend ./backend
COPY web ./web

RUN mkdir -p data

ENV HOST=0.0.0.0
ENV PORT=8765
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD python app.py --host ${HOST} --port ${PORT}
