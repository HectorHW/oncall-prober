FROM python:3.10-alpine

COPY slo.requirements .
RUN pip install -r slo.requirements
COPY slo-checker.py .
ENTRYPOINT [ "./slo-checker.py" ]