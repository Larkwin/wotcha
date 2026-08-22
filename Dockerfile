FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "wotcha.runtime"]
