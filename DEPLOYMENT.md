# RevenueBringer Website Deployment

The website is built with Flask and ready for deployment.

## Local Testing

1. Install dependencies: `pip install -r requirements.txt`
2. Run the server: `python server.py`
3. Open `http://localhost:5000`

## Public Deployment

### Option 1: Render (Free)

1. Create a GitHub repository
2. Upload all files from `website/` folder to the repo
3. Go to [render.com](https://render.com) and sign up
4. Create a new Web Service
5. Connect your GitHub repo
6. Set build command: `pip install -r requirements.txt`
7. Set start command: `python server.py`
8. Deploy

### Option 2: Railway (Free)

1. Create a GitHub repository
2. Upload files
3. Go to [railway.app](https://railway.app)
4. Connect GitHub repo
5. Railway will auto-detect Flask
6. Deploy

### Option 3: Heroku (Free Tier)

1. Install Git
2. Create GitHub repo and push files
3. Create Heroku app
4. Connect GitHub repo
5. Set buildpack to Python
6. Deploy

### Option 4: Ngrok for Temporary Public Access

1. Download ngrok from [ngrok.com](https://ngrok.com)
2. Run: `ngrok http 5000`
3. Use the provided URL (e.g., `https://random.ngrok.io`)

## Files

- `index.html` - Landing page
- `style.css` - Styles
- `server.py` - Flask backend
- `contacts.json` - Contact submissions database
- `requirements.txt` - Python dependencies

The contact form saves submissions to `contacts.json`. View all leads at `/admin/contacts`.