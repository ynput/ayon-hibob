FROM python:3.11-alpine
ENV PYTHONUNBUFFERED=1

RUN mkdir /service
COPY ./ayon_sync /service/ayon_sync
COPY ./pyproject.toml /service/pyproject.toml
WORKDIR /service

RUN pip install .

CMD ["python", "-m", "ayon_sync"]
