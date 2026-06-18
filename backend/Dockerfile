# Use the official Python + Playwright image
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Set the working directory
WORKDIR /app

# Copy the requirements file and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your actual backend code into the container
COPY . .

# Start the FastAPI server on the port Render assigns
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}