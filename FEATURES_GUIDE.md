# GeoView - Enhanced Features Guide

## 🎉 New Features Added

Your GeoView application now includes two major professional features:

---

## 1. 🔐 Professional Secure Authentication System

### Features:
- ✅ **User Registration with Validation**
  - Unique username enforcement (no duplicates)
  - Email validation and uniqueness
  - Secure password hashing using bcrypt
  - Optional phone number for SMS notifications

- ✅ **Secure Login System**
  - Username/password authentication
  - Session management
  - User-specific data isolation

- ✅ **Advanced Password Recovery**
  - Forgot password functionality
  - OTP (One-Time Password) verification
  - Two-channel OTP delivery:
    - 📧 Email (default)
    - 📱 SMS (via Twilio)
  - 10-minute OTP expiration window
  - Password reset with confirmation

### How to Use:

#### **Sign Up**
1. Go to `http://localhost:8000/register`
2. Enter a unique username (min 3 characters)
3. Provide email address (will be verified for OTP)
4. Create a strong password (min 6 characters)
5. Click "Create Account"

#### **Login**
1. Go to `http://localhost:8000/login`
2. Enter your username and password
3. Click "Sign In"

#### **Forgot Password**
1. Click "Forgot Password?" on login page
2. Enter your username or email
3. Receive OTP via email or SMS
4. Enter the 6-digit OTP code
5. Set your new password
6. Login with new password

---

## 2. 🛰️ Automatic Satellite Image Analysis with Area Search

### Features:
- ✅ **Smart Area Search with Auto-Complete**
  - Type any location name (city, region, coordinates)
  - Google-like search suggestions
  - Cached results for quick lookups
  - Supports worldwide locations via Nominatim/OpenStreetMap

- ✅ **Automatic Satellite Image Fetching**
  - Searches publicly available Sentinel-2 scenes for the requested location and date
  - Rejects scenes with less than 80% clear area coverage
  - Shows actual acquisition dates when reported by the source

- ✅ **Automatic Change Detection**
  - Compares satellite images from different time periods
  - Highlights visible image differences
  - Reports scene dates and cloud cover when available from the imagery provider
  - Rejects scenes with less than 80% clear pixels in the analyzed area and excludes cloudy or unclear pixels from comparisons
  - Optional Earth Engine temporal assessment compares Dynamic World monthly class probabilities across year-over-year windows
  - Reports likely regional built-up, tree-cover, water, or bare-ground trends without asserting exact cause or legality

- ✅ **Real-Time Analysis Results**
  - Change magnitude visualization
  - Geographic coordinates
  - Neutral image-change assessment
  - Alerts indicate image differences only, not illegal activity

### How to Use:

#### **Automatic Area Analysis**
1. Login to your account
2. Go to `Dashboard` → Click "Auto Analyze"
3. In the search box, type an area name:
   - City name: "Tokyo", "New York", "Mumbai"
   - Region: "Amazon", "Sahara", "Siberia"
   - Coordinates: "Delhi", "Singapore"
4. Wait for auto-complete suggestions to appear
5. Click on a suggestion or type exactly
6. Set the time range (how many days back to compare)
7. Click "Analyze Area"
8. System will:
   - Fetch satellite images from Sentinel Hub
   - Analyze changes automatically
   - Display results with change magnitude

#### **Understanding Results**
- **Change Value**: Number of pixels changed (higher = more changes)
- **Alert Level**:
  - 🔴 High (>5000 pixels) - Significant changes detected
  - 🟡 Medium (1000-5000 pixels) - Moderate changes
  - 🟢 Low (<1000 pixels) - Minor changes

---

## 🔧 Configuration Required

### For Automatic Satellite Images (Sentinel Hub):

1. Get Sentinel Hub credentials:
   - Visit: https://www.sentinel-hub.com/
   - Create account and get Client ID & Secret
   - Add to `.env` file:
   ```
   SENTINELHUB_CLIENT_ID=your_client_id
   SENTINELHUB_CLIENT_SECRET=your_client_secret
   ```

### For OTP via Email (Gmail):

1. Enable 2-Factor Authentication on Gmail
2. Generate App Password: https://myaccount.google.com/apppasswords
3. Add to `.env` file:
   ```
   ALERT_EMAIL=your-email@gmail.com
   ALERT_EMAIL_PASSWORD=your-app-password
   ```

### For OTP via SMS (Twilio) - Optional:

1. Create Twilio account: https://www.twilio.com/
2. Get Account SID, Auth Token, and phone numbers
3. Add to `.env` file:
   ```
   TWILIO_ACCOUNT_SID=your-account-sid
   TWILIO_AUTH_TOKEN=your-auth-token
   TWILIO_FROM_NUMBER=+1234567890
   TWILIO_TO_NUMBER=user-phone-number
   ```

---

## 📊 Database Changes

The application now includes enhanced tables:

### New Tables:
- `users` - User account information with password hashes
- `otp_tokens` - OTP generation and verification records
- `area_cache` - Cached geolocation results for fast lookups

### Updated Tables:
- `analysis` - Now includes `user_id`, `area_name`, `latitude`, `longitude`
- `reports` - Now includes `user_id` for user-specific reports
- `landowners` - Now includes `user_id` for user-specific data

---

## 🚀 Running the App

```bash
cd "c:\Users\tejas\Desktop\satellite image"
python app.py
```

Then access at: **http://localhost:8000**

---

## 🔐 Security Features

✅ Password Hashing with bcrypt
✅ Session-based authentication
✅ OTP-based identity verification
✅ User data isolation
✅ Unique username enforcement
✅ Email/Phone based recovery
✅ 10-minute OTP expiration

---

## 📱 Workflow Examples

### Example 1: First-Time User
1. Visit http://localhost:8000
2. Redirected to login → Click "Sign Up Here"
3. Create account with unique username
4. Login with new credentials
5. Dashboard loads
6. Click "Auto Analyze" to start using

### Example 2: Area Analysis
1. Login to dashboard
2. Click "Auto Analyze"
3. Type "Amazon Rainforest" in search
4. Select from suggestions
5. Set comparison period to 90 days
6. System fetches satellite images
7. Analyzes changes automatically
8. Shows results with coordinates and change data

### Example 3: Lost Password Recovery
1. On login page, click "Forgot Password?"
2. Enter username or email
3. Receive 6-digit OTP via email
4. Enter OTP
5. Create new password
6. Login with new password

---

## 📝 Files Modified/Created

### New Files:
- `templates/register.html` - Registration page
- `templates/forgot_password.html` - Password recovery
- `templates/verify_otp.html` - OTP verification
- `templates/reset_password.html` - New password creation
- `templates/auto_analyze.html` - Area search and auto-analysis

### Modified Files:
- `app.py` - Complete rewrite with new features
- `templates/login.html` - Updated with new links
- `templates/dashboard.html` - Updated navigation

### Backup:
- `app_old.py` - Backup of original app.py

---

## 🐛 Troubleshooting

### Issue: "Area not found"
**Solution**: Try with a more specific location name or major city

### Issue: "Email not configured"
**Solution**: Set ALERT_EMAIL and ALERT_EMAIL_PASSWORD in .env file

### Issue: "OTP not received"
**Solution**: Check email spam folder or configure SMS in .env

### Issue: "Username already exists"
**Solution**: Choose a different unique username

### Issue: "Sentinel Hub error"
**Solution**: Verify CLIENT_ID and CLIENT_SECRET in .env are correct

---

## 📊 Analytics & Dashboard

The dashboard now shows:
- User-specific analysis history
- Change statistics (average, max, min)
- Area-based analysis grouping
- Interactive charts and reports

---

## 🎯 Next Steps (Optional Enhancements)

1. **Email Notifications**
   - Notifications for observed image differences
   - Daily summary emails

2. **Advanced Mapping**
   - Interactive maps with change overlays
   - Heatmaps of detected changes
   - Geographic clustering

3. **API Integration**
   - REST API for external access
   - Webhook notifications
   - Data export features

4. **ML Enhancement**
   - The optional temporal assessment uses the pretrained Dynamic World land-cover model; it is not a locally trained event-cause classifier
   - A supervised classifier for mining, natural change, or other causes requires suitable licensed and labeled time-series examples
   - Legal-status checks require authoritative jurisdiction-specific boundaries, permits, or enforcement records; none are connected for Bengaluru yet

---

## 📞 Support

For issues or questions:
1. Check the troubleshooting section
2. Verify .env configuration
3. Check Flask console for error messages
4. Review database schema changes

---

**GeoView v2.0 - Advanced Satellite Analysis System**
Built with Flask, Sentinel Hub, and Modern Security Practices
