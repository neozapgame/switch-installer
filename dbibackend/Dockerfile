FROM python:3.11-slim

WORKDIR /app

# libusb untuk pyusb, inotify-tools untuk hotplug detection
RUN apt-get update && \
    apt-get install -y libusb-1.0-0 inotify-tools && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pyusb

COPY dbibackend.py .

VOLUME ["/games"]

CMD ["python3", "dbibackend.py"]
