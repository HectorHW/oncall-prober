FROM python:3.10-alpine

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY slo-checker.py .
ENTRYPOINT [ "./slo-checker.py" ]