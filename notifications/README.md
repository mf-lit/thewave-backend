# Notifications System Backend

Backend service for managing session availability notifications for the waveform mobile app.

## Overview

This system consists of two main components:

1. **Flask API** - RESTful endpoints for clients to manage their notifications
2. **Daemon Service** - Periodically checks session availability and sends notifications

## Architecture

- **Storage**: DynamoDB (via LocalStack for local development)
- **API Framework**: Flask
- **Language**: Python 3.11+

## Setup

### Prerequisites

- Python 3.11 or higher
- UV package manager
- LocalStack running on `http://192.168.1.1:8001` (or configure via `AWS_ENDPOINT_URL`)

### Installation

```bash
# Install dependencies using UV
uv sync
```

### Environment Variables

- `AWS_ENDPOINT_URL` - DynamoDB endpoint (default: `http://192.168.1.1:8001`)
- `CALENDAR_API_URL` - Upstream calendar API URL (default: `http://localhost:5000/calendar`)

## Usage

### Running the API Server

```bash
python -m src.api.app
```

The API will be available at `http://localhost:5001`

### Running the Daemon

```bash
python -m src.daemon.scheduler
```

The daemon will:
- Check availability every 10 minutes
- Automatically delete notifications for past sessions
- Send notifications when conditions are met (currently logs to console)

## API Endpoints

All endpoints are scoped to a client UUID.

### Create Notification

```http
POST /clients/{client_id}/notifications
Content-Type: application/json

{
  "date": "2025-12-30",
  "time": "10:00",
  "side": "left",
  "notification_type": "below_threshold",
  "thresholds": [5, 2]
}
```

**Notification Types:**
- `below_threshold`: Notify when availability falls below specified thresholds
- `above_zero`: Notify when availability increases above zero

### List Notifications

```http
GET /clients/{client_id}/notifications
```

### Delete Notification

```http
DELETE /clients/{client_id}/notifications/{notification_id}
```

## DynamoDB Schema

**Table**: `waveform-notifications`

- **Partition Key**: `client_id` (String, UUID)
- **Sort Key**: `notification_id` (String, UUID)
- **GSI**: `performance_ak-index` on `performance_ak`

## Development

The project structure:

```
notifications/
├── pyproject.toml
├── src/
│   ├── api/          # Flask API endpoints
│   ├── daemon/       # Background daemon service
│   ├── storage/      # DynamoDB operations
│   └── utils/        # Calendar API client
```

