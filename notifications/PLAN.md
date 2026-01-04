We're going to build the backend for a notifications systes. The waveform mobile app (source code is in ../waveform) will be the client. We'll be using Python with UV and Flask. The user of the client will request to recieve notifications for when specific "sessions" become nearly fully booked, or for when a session that was previously fully booked has space become available.

There are two main components to build.

## API

An API that clients will call to manage their notifications. There are three actions a client may want to carry out:
1) Create a notification for a session. A session is identified by it's date & start time & side (left, right or none). Clients can request to receive a notification when availability falls below a specified threshold (and optionally when availability falls further), or they can request to receive a notification when availability increases above zero.
2) List all their configured sessions.
3) Delete an current notification.

DynamoDB will be used for storing state. For now a localstack DynamoDB service can be used (address is http://192.168.1.1:8001)

## Daemon

A daemon service that will periodically (10 minutes) check availability of all sessions for which notifications exist. Availability can be checked via the "upstream API" currently available at http://localhost:5000/calendar , the following parameters are excepted by the API: "dateFrom" & "numberOfDays". (e.g. http://localhost:5000/calendar?dateFrom=2025-12-30&numberOfDays=1)

The daemon service automatically will delete any notification that is for a session in the past.

The daemon service will send notifications by calling an API that doesn't exist yet. For now just put a placeholder in the code and log notifications.
