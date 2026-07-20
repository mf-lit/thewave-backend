# FCM Token Management API

## Overview

Clients can register, check, and delete their Firebase Cloud Messaging (FCM) tokens via these endpoints. All endpoints are scoped to a client UUID.

**Base URL**: `http://localhost:5001` (or your server URL)

## Endpoints

### 1. Register/Update FCM Token

**PUT** `/clients/{client_id}/fcm-token`

Register or update the FCM token for a client.

**Path Parameters:**
- `client_id` (UUID, required) - The client's unique identifier

**Request Body:**
```json
{
  "fcm_token": "dK3jF9...long_token_string..."
}
```

**Request Headers:**
- `Content-Type: application/json`

**Response (200 OK):**
```json
{
  "message": "FCM token saved successfully",
  "updated_at": "2026-01-04T08:50:08.080450"
}
```

**Error Responses:**
- `400 Bad Request` - Invalid client_id format, missing fcm_token, or invalid token format
- `500 Internal Server Error` - Server error

**Token Validation:**
- Token must be 140-200 characters long
- Token can only contain: alphanumeric characters, colons (`:`), hyphens (`-`), and underscores (`_`)
- Token must be a non-empty string

---

### 2. Check FCM Token Existence

**GET** `/clients/{client_id}/fcm-token`

Check if a client has a registered FCM token. Does not return the actual token value for security.

**Path Parameters:**
- `client_id` (UUID, required) - The client's unique identifier

**Response (200 OK):**
```json
{
  "has_token": true
}
```

**Error Responses:**
- `400 Bad Request` - Invalid client_id format
- `404 Not Found` - No FCM token registered for this client
- `500 Internal Server Error` - Server error

---

### 3. Delete FCM Token

**DELETE** `/clients/{client_id}/fcm-token`

Remove the FCM token for a client (e.g., when user logs out or disables notifications).

**Path Parameters:**
- `client_id` (UUID, required) - The client's unique identifier

**Response (200 OK):**
```json
{
  "message": "FCM token deleted successfully"
}
```

**Error Responses:**
- `400 Bad Request` - Invalid client_id format
- `404 Not Found` - No FCM token found for this client
- `500 Internal Server Error` - Server error

---

## Example Usage

### Register Token
```bash
curl -X PUT "http://localhost:5001/clients/550e8400-e29b-41d4-a716-446655440000/fcm-token" \
  -H "Content-Type: application/json" \
  -d '{"fcm_token": "your_fcm_token_here"}'
```

### Check Token
```bash
curl -X GET "http://localhost:5001/clients/550e8400-e29b-41d4-a716-446655440000/fcm-token"
```

### Delete Token
```bash
curl -X DELETE "http://localhost:5001/clients/550e8400-e29b-41d4-a716-446655440000/fcm-token"
```

## Notes

- The same `client_id` used for notifications endpoints should be used for FCM token management
- Tokens can be updated by calling PUT with a new token (replaces the old one)
- The GET endpoint only confirms token existence, it does not return the token value
- All endpoints validate the `client_id` format (must be a valid UUID)
- FCM tokens are validated for format and length before being stored

