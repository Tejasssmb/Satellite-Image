# Satellite Change Detection System

A Flask-based web application for detecting visible differences between satellite images.

## Features

- **Temporal Land-cover Analysis (optional)**: Compare monthly probabilities from the pre-trained Dynamic World model across two year-over-year windows
- **Visual Change Display**: See highlighted changes with red overlays
- **Scene Metadata**: Shows actual scene dates and cloud cover when provided by the imagery source
- **Cloud Screening**: Rejects scenes with less than 80% clear pixels over the analyzed area and excludes unclear pixels from change counts
- **Assessment Limits**: Image differences alone cannot distinguish natural change from development or determine legality
- **Change Notifications**: Neutral web and browser alerts for observed image differences

### Current model status

The app now offers an optional temporal evidence mode for the Bengaluru pilot. It queries the pre-trained Google Dynamic World V1 land-cover model through Earth Engine, aggregates its nine class probabilities into monthly regional observations, and compares the latest 12-month window with the preceding 12-month window. It can report potential built-up expansion, tree-cover decline, water-area change, or bare/sparse-ground increase when there are enough clear observations. This is a pretrained per-image model plus a temporal comparison, **not a locally trained temporal event classifier**. It does not identify mining as a cause, distinguish natural from human-caused change reliably, or determine legality. Legal status remains unverified until official Bengaluru/Karnataka records are integrated.

Dynamic World is available under CC BY 4.0. Attribute the dataset as: “This dataset is produced for the Dynamic World Project by Google in partnership with National Geographic Society and the World Resources Institute.”

## Installation

1. Clone or download the project
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

### Optional temporal analysis setup

1. Install the optional Earth Engine client:
   ```bash
   pip install -r requirements-temporal.txt
   ```
2. Create or select a Google Cloud project, enable Earth Engine access for it, and authenticate the account running GeoView:
   ```bash
   earthengine authenticate
   earthengine set_project YOUR_GOOGLE_CLOUD_PROJECT_ID
   ```
3. Add the project ID to `.env`:
   ```text
   EARTH_ENGINE_PROJECT=YOUR_GOOGLE_CLOUD_PROJECT_ID
   ```
4. Restart GeoView. In **Auto Analyze**, choose at least two years of dates and enable **Analyze the multi-year land-cover time series**. The analysis requires at least eight clear monthly observations in each of the two year-over-year windows; otherwise it reports insufficient evidence.

The Earth Engine account and Cloud project must be approved/configured by their owner. GeoView does not receive or create that access automatically.

## Configuration

### Email Setup (Gmail)
1. Enable 2-Factor Authentication on your Gmail account
2. Generate an App Password: https://myaccount.google.com/apppasswords
3. Update `.env`:
   ```
   ALERT_EMAIL=your-email@gmail.com
   ALERT_EMAIL_PASSWORD=your-app-password
   ALERT_RECEIVER_EMAIL=admin@example.com
   ```

### SMS Setup (Twilio)
1. Sign up for Twilio: https://www.twilio.com/
2. Get your Account SID, Auth Token, and phone numbers
3. Update `.env`:
   ```
   TWILIO_ACCOUNT_SID=your-sid
   TWILIO_AUTH_TOKEN=your-token
   TWILIO_FROM_NUMBER=+1234567890
   TWILIO_TO_NUMBER=+0987654321
   ```

## Usage

1. Run the application:
   ```bash
   python app.py
   ```

2. Open http://127.0.0.1:8000 in your browser

3. Upload "before" and "after" satellite images

4. The system will:
   - Detect visible image differences
   - Report the requested date range and available scene metadata
   - Show the analyzed location on a map
   - Optionally report regional land-cover probability trends from Dynamic World when Earth Engine is configured
   - Keep legality unverified; confirm potential changes with authoritative records or field inspection
   - Show a neutral alert for significant image differences

The temporal model option appears on **Auto Analyze**. It uses the selected multi-year period, not only the two endpoint images.

## API Endpoints

- `GET /` - Home page
- `GET /upload` - Upload form
- `POST /upload` - Process uploaded images
- `GET /analyze` - Anomaly analysis form
- `POST /analyze` - Process anomaly analysis
- `GET /dashboard` - View analysis history
- `GET /api/analysis` - Get analysis data
- `GET /api/stats` - Get statistics
- `GET /analyze` - Upload images with geographic coordinates and map anomaly boxes
- `GET /report` - Submit a citizen report
- `GET /reports` - View the signed-in user's reports
- `GET /landowners` - Manage the signed-in user's landowners
- `GET /visualization` - View the interactive visualization
- `GET /map` - View mapped anomaly results

## Alert System

When a significant image difference is detected, the system can trigger:

1. **Web Alerts**: Sound + visual notifications for observed image changes; they do not constitute a finding of illegal activity

## Security Notes

- Store credentials securely using environment variables
- Use App Passwords for Gmail instead of regular passwords
- Regularly rotate API keys and tokens
- Monitor notification usage to avoid service limits

## Production Deployment

For production use:

1. Set a strong `SECRET_KEY` and set `SESSION_COOKIE_SECURE=1` behind HTTPS
2. Use a production WSGI server (Waitress on Windows, Gunicorn or uWSGI on Linux)
3. Run with `debug=False`; the built-in server is for local development only
4. Set up proper logging
5. Configure HTTPS
6. Use environment-specific configuration
7. Set up monitoring and alerting for the application itself

## License

This project is for educational and demonstration purposes.