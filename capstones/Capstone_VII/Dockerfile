FROM python:3.11-buster

RUN apt-get update ; \
    apt-get install -y graphviz

RUN pip install --upgrade pip

COPY requirements.txt /req/
RUN pip install -r /req/requirements.txt

VOLUME /work 
WORKDIR /work
EXPOSE 8888

CMD ["jupyter", "lab", "--allow-root", "--ip", "0.0.0.0", "--port", "8888"]