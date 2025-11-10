# Event Management System

A full-stack event management system with Python backend and SQLite database.

## Features
- User authentication (login/register)
- Event management (CRUD operations)
- Admin dashboard
- Image uploads for events
- Featured events section
- Responsive frontend
- Optional paid registrations powered by Razorpay checkout

## Setup Instructions

1. **Install Python**
   - Make sure you have Python 3.8+ installed

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   - Create a `.env` file (see [Environment Variables](#environment-variables))

5. **Initialize the database**
   ```bash
   flask shell
   >>> from app import db
   >>> db.create_all()
   >>> exit()
   ```

6. **Run the application**
   ```bash
   flask run
   ```

7. **Access the application**
   - Frontend: http://localhost:5000
   - API: http://localhost:5000/api/events

## Project Structure

```
.
├── app.py                 # Main application file
├── requirements.txt       # Python dependencies
├── .env                  # Environment variables
├── public/               # Frontend files
│   ├── index.html
│   ├── css/
│   ├── js/
│   └── uploads/          # Uploaded images
└── instance/
    └── events.db        # SQLite database (created automatically)
```

## API Endpoints

- `GET /api/events` - Get all events
- `GET /api/events/featured` - Get featured events
- `POST /api/events` - Create new event (admin only)
- `PUT /api/events/<id>` - Update event (admin only)
- `DELETE /api/events/<id>` - Delete event (admin only)
- `POST /api/payments/create-order` - Generate a Razorpay order for paid registrations

## Environment Variables

Create a `.env` file in the root directory with the following variables:

```
FLASK_APP=app.py
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
JWT_SECRET_KEY=your-jwt-secret

# Razorpay (optional for paid events)
RAZORPAY_KEY_ID=your-razorpay-key
RAZORPAY_KEY_SECRET=your-razorpay-secret

# Configure default currency if needed (defaults to INR)
# PAYMENT_CURRENCY=INR

```

If you prefer event-specific Razorpay credentials, organizers can supply their own key pair when creating events. Otherwise, the global keys above are used as a fallback.

## Payment Test Mode (New!)

EventFlow now includes a **Payment Test Mode** feature that allows you to demonstrate and test payment flows without requiring real Razorpay credentials or processing actual transactions.

### Quick Start with Test Mode

1. Open `public/config.js`
2. Set `USE_PAYMENT_TEST_MODE: true`
3. Visit `payment-demo.html` to see it in action
4. No Razorpay credentials needed!

### Features

✨ **Beautiful Payment Animations** - Professional processing and success animations  
🚀 **Instant Setup** - Works immediately without any configuration  
🎭 **Perfect for Demos** - Showcase your platform without payment complications  
🔒 **Safe Testing** - No real money involved, no credentials exposed

### How to Use

**For Development/Demos:**
```javascript
// In public/config.js
USE_PAYMENT_TEST_MODE: true
```
Users will see a beautiful animation simulating payment processing. No Razorpay integration required!

**For Production:**
```javascript
// In public/config.js
USE_PAYMENT_TEST_MODE: false
```
Real Razorpay integration with actual payment processing.

📖 **Full Documentation:** See [PAYMENT_TEST_MODE.md](PAYMENT_TEST_MODE.md) for complete details.

🎮 **Try the Demo:** Visit `http://localhost:5000/payment-demo.html` after starting the server.

## Enabling paid registrations

1. Obtain Razorpay test or live credentials and add them to `.env` (or provide them per-event in the admin dashboard).
2. When creating or editing an event, toggle **Enable Payments** and set a **Registration Fee** (in ₹).
3. Attendees will complete checkout using Razorpay and registrations are only confirmed after signature verification.

> **Tip:** Keep your Razorpay secret key out of version control and rotate it periodically.
