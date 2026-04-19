
# Use an official Python runtime as a parent image
FROM python:3.10-buster

# Install AWS CLI
RUN apt-get update && \
    apt-get install -y unzip groff mandoc && \
    apt-get clean  && \
    rm -rf /var/lib/apt/lists/*  && \
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"  && \
    unzip awscliv2.zip  && \
    ./aws/install

# Install any needed packages specified in requirements.txt
ADD requirements.txt . 
RUN pip install --no-cache-dir -r requirements.txt

# Install glue kernel
RUN install-glue-kernels

# Set the working directory in the container to /workdir
WORKDIR /workdir
