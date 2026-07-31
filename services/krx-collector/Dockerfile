FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 -r requirements.txt
RUN sed -i 's/            print(f"Error occurred in {func.__name__}: {e}")/            logging.debug("pykrx %s failed: %s", func.__name__, e)/; /            logging\.info(args, kwargs)/d; /            logging\.info(e)/d' /usr/local/lib/python3.11/site-packages/pykrx/website/comm/util.py

COPY . .

CMD ["python", "-m", "app.main"]
