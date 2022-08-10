FROM python:3.9

RUN apt-get update

WORKDIR /app/

COPY . ./

RUN pip3 install -r requirements.txt

EXPOSE 5000

CMD ["python3",  "-u", "./main.py"]