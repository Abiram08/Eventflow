from flask import Flask, render_template, request, jsonify, send_from_directory, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from flask_migrate import Migrate
from datetime import datetime, timedelta
import os
import jwt
import json
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import razorpay

try:
    from razorpay.errors import SignatureVerificationError, RazorpayError
except ImportError:
    from razorpay.errors import (
        SignatureVerificationError,
        BadRequestError,
        GatewayError,
        ServerError,
    )

    RazorpayError = (BadRequestError, GatewayError, ServerError)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__, static_folder='public')

# Configure CORS
CORS(app, 
     resources={
         r"/api/*": {
             "origins": ["http://127.0.0.1:5000", "http://localhost:5000", "*"],
             "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
             "allow_headers": ["Content-Type", "Authorization", "Accept"],
             "supports_credentials": True
         }
     })

# Initialize JWT
jwt_manager = JWTManager(app)

# Configuration
# Use an absolute path for the SQLite database to ensure persistence
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.abspath("events.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('public', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'jwt-secret-key-123')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_razorpay_credentials(event=None):
    """Return the Razorpay credentials for an event or global defaults."""
    key_id = None
    key_secret = None

    if event:
        key_id = (event.razorpay_key_id or '').strip() or None
        key_secret = (event.razorpay_key_secret or '').strip() or None

    if not key_id:
        key_id = os.getenv('RAZORPAY_KEY_ID')
    if not key_secret:
        key_secret = os.getenv('RAZORPAY_KEY_SECRET')

    return key_id, key_secret


def get_razorpay_client(key_id, key_secret):
    client = razorpay.Client(auth=(key_id, key_secret))
    client.set_app_details({"title": "EventFlow", "version": "1.0"})
    return client

# Initialize database
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    user_type = db.Column(db.String(20), nullable=False, default='student')  # 'student', 'organizer', 'admin'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        """Create hashed password."""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check hashed password."""
        return check_password_hash(self.password_hash, password)
    
    def generate_auth_token(self):
        payload = {
            'id': self.id,
            'username': self.username,
            'is_admin': self.is_admin,
            'user_type': self.user_type,
            'exp': datetime.utcnow() + app.config['JWT_ACCESS_TOKEN_EXPIRES']
        }
        return jwt.encode(payload, app.config['JWT_SECRET_KEY'], algorithm='HS256')

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=True)  # For multi-day events
    location = db.Column(db.String(200), nullable=False)
    image_url = db.Column(db.String(500))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_featured = db.Column(db.Boolean, default=False)
    
    # Event details
    club_name = db.Column(db.String(200))
    department = db.Column(db.String(200))
    timing = db.Column(db.String(100))
    registration_type = db.Column(db.String(100))
    event_list = db.Column(db.Text)
    contact = db.Column(db.String(200))
    event_type = db.Column(db.String(100), default='Workshop')
    category = db.Column(db.String(100), default='General')
    tags = db.Column(db.Text)  # JSON string for tags
    
    # Registration settings
    max_capacity = db.Column(db.Integer, default=0)  # 0 means unlimited
    registration_deadline = db.Column(db.DateTime, nullable=True)
    registration_fee = db.Column(db.Float, default=0.0)
    requires_approval = db.Column(db.Boolean, default=False)
    allow_waitlist = db.Column(db.Boolean, default=True)
    external_registration_url = db.Column(db.String(500))  # For Google Forms etc

    # Payment settings (Razorpay integration)
    razorpay_key_id = db.Column(db.String(100))  # Razorpay Key ID for the organizer
    razorpay_key_secret = db.Column(db.String(100))  # Razorpay Key Secret (stored securely)
    organizer_account_id = db.Column(db.String(100))  # Razorpay account ID
    payment_enabled = db.Column(db.Boolean, default=False)  # Whether payment is enabled for this event
    
    # Admin approval
    approved = db.Column(db.Boolean, default=False)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    
    # Status
    status = db.Column(db.String(50), default='active')  # active, cancelled, postponed, completed
    
    # Relationships
    registrations = db.relationship('EventRegistration', backref='event', lazy=True, cascade='all, delete-orphan')
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_events')
    approver = db.relationship('User', foreign_keys=[approved_by])

class EventRegistration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='registered')  # registered, waitlisted, cancelled, attended
    
    # Registration details
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20))
    department = db.Column(db.String(100))
    college = db.Column(db.String(200))
    year = db.Column(db.String(10))
    participating_events = db.Column(db.Text)  # Comma-separated list of events the attendee wants to participate in
    organization = db.Column(db.String(200))
    dietary_requirements = db.Column(db.Text)
    special_needs = db.Column(db.Text)
    
    # Payment info (if applicable)
    payment_status = db.Column(db.String(50), default='pending')  # pending, completed, failed, refunded
    payment_method = db.Column(db.String(50))
    payment_reference = db.Column(db.String(100))
    
    # Admin fields
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    check_in_time = db.Column(db.DateTime, nullable=True)
    check_in_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref='event_registrations')
    approved_by_user = db.relationship('User', foreign_keys=[approved_by])
    checked_in_by_user = db.relationship('User', foreign_keys=[check_in_by])
    
    # Constraints
    __table_args__ = (db.UniqueConstraint('event_id', 'user_id', name='unique_event_user'),)

class EventCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    color = db.Column(db.String(7), default='#6b21a8')  # Hex color
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EventTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    template_data = db.Column(db.Text)  # JSON string with event template
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_public = db.Column(db.Boolean, default=False)
    
    creator = db.relationship('User', backref='event_templates')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # registration, approval, reminder, cancellation
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Related objects
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=True)
    registration_id = db.Column(db.Integer, db.ForeignKey('event_registration.id'), nullable=True)
    
    user = db.relationship('User', backref='notifications')
    related_event = db.relationship('Event', backref='notifications')
    related_registration = db.relationship('EventRegistration', backref='notifications')

# Authentication decorators
def organizer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'message': 'Missing or invalid token'}), 401
            
        token = auth_header.split(' ')[1]
        try:
            payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
            user_type = payload.get('user_type')
            if user_type not in ['organizer', 'admin']:
                return jsonify({'message': 'Organizer access required'}), 403
            return f(*args, **kwargs)
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token'}), 401
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'message': 'Missing or invalid token'}), 401
            
        token = auth_header.split(' ')[1]
        try:
            payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
            if not payload.get('is_admin', False):
                return jsonify({'message': 'Admin access required'}), 403
            return f(*args, **kwargs)
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token'}), 401
    return decorated_function

# Routes for serving frontend files
@app.route('/')
def serve_index():
    return send_from_directory('public', 'index.html')

# Serve static files with better error handling
@app.route('/<path:path>')
def serve_static(path):
    try:
        # Block direct access to .env and other sensitive files
        if any(path.startswith(blocked) for blocked in ['.env', 'instance/', 'venv/']):
            abort(404)
        return send_from_directory('public', path)
    except Exception as e:
        # If file not found, serve index.html for SPA routing
        if 'index.html' in os.listdir('public'):
            return send_from_directory('public', 'index.html')
        raise e

# Admin Authentication Routes
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json() or {}
    payment_data = data.get('payment')
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'message': 'Username and password are required'}), 400
    
    # Find admin user by username or email
    user = User.query.filter(
        (User.username == username) | (User.email == username),
        User.is_admin == True
    ).first()
    
    if not user or not user.check_password(password):
        return jsonify({'message': 'Invalid username or password'}), 401
    
    # Use the same token generation method as regular login
    token = create_access_token(identity=user.id, additional_claims={
        'id': user.id,
        'is_admin': user.is_admin,
        'username': user.username,
        'user_type': user.user_type
    })
    
    return jsonify({
        'message': 'Login successful',
        'token': token,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'user_type': user.user_type
        }
    })

@app.route('/api/admin/verify', methods=['GET'])
@admin_required
def verify_admin():
    return jsonify({'message': 'Token is valid', 'is_admin': True})

@app.route('/api/auth/verify', methods=['GET'])
@jwt_required()
def verify_user():
    # Get the current user ID from the JWT token
    current_user_id = get_jwt_identity()
    
    # Get the user from the database
    user = User.query.get(current_user_id)
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    return jsonify({
        'message': 'Token is valid',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'user_type': user.user_type
        }
    })

# API Routes
@app.route('/api/events', methods=['POST'])
@organizer_required
def create_event():
    # Get the current user ID from the JWT token using our custom method
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    
    # Check if the post request has the file part
    if 'poster' not in request.files and 'image' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files.get('poster') or request.files.get('image')
    
    # If user does not select file, browser also
    # submit an empty part without filename
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Create a unique filename to prevent overwriting
        unique_filename = f"{int(datetime.now().timestamp())}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        
        # Ensure the upload directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        file.save(filepath)
        
        # Get other form data
        title = request.form.get('eventName') or request.form.get('title')
        description = request.form.get('description')
        location = request.form.get('venue') or request.form.get('location')
        date_str = request.form.get('date')
        is_featured = request.form.get('is_featured', 'false').lower() == 'true'
        
        # Additional fields from the form
        club_name = request.form.get('clubName')
        department = request.form.get('department')
        timing = request.form.get('timing')
        registration_type = request.form.get('registrationType')
        event_list = request.form.get('eventList')
        contact = request.form.get('contact')
        event_type = request.form.get('eventType', 'Workshop')
        max_capacity = request.form.get('max_capacity', type=int, default=0)

        # Payment fields
        payment_enabled = request.form.get('payment_enabled', 'false').lower() == 'true'
        registration_fee = request.form.get('registration_fee', type=float, default=0.0)
        razorpay_key_id = request.form.get('razorpay_key_id')
        razorpay_key_secret = request.form.get('razorpay_key_secret')
        organizer_account_id = request.form.get('organizer_account_id')
            
        try:
            # Try parsing date with time first, then date only
            try:
                event_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                event_date = datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError) as e:
            return jsonify({'error': f'Invalid date format. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM. Error: {str(e)}'}), 400
        
        # Create new event
        new_event = Event(
            title=title,
            description=description,
            date=event_date,
            location=location,
            image_url=f'/uploads/{unique_filename}',
            created_by=current_user_id,
            is_featured=is_featured,
            club_name=club_name,
            department=department,
            timing=timing,
            registration_type=registration_type,
            event_list=event_list,
            contact=contact,
            event_type=event_type,
            max_capacity=max_capacity,
            payment_enabled=payment_enabled,
            registration_fee=registration_fee,
            razorpay_key_id=razorpay_key_id,
            razorpay_key_secret=razorpay_key_secret,
            organizer_account_id=organizer_account_id
        )
        
        db.session.add(new_event)
        db.session.commit()
        
        return jsonify({
            'message': 'Event created successfully',
            'event': {
                'id': new_event.id,
                'title': new_event.title,
                'description': new_event.description,
                'date': new_event.date.isoformat(),
                'location': new_event.location,
                'image_url': new_event.image_url,
                'is_featured': new_event.is_featured,
                'club_name': new_event.club_name,
                'department': new_event.department,
                'timing': new_event.timing,
                'registration_type': new_event.registration_type,
                'event_list': new_event.event_list,
                'contact': new_event.contact,
                'event_type': new_event.event_type
            }
        }), 201
    else:
        return jsonify({'error': 'File type not allowed. Allowed types are: png, jpg, jpeg, gif'}), 400
    

# Public endpoint to get all events (no authentication required)
@app.route('/api/events', methods=['GET'])
def get_all_events():
    # Only show approved events to public
    events = Event.query.filter_by(approved=True).order_by(Event.date.desc()).all()
    return jsonify([{
        'id': event.id,
        'eventName': event.title,  # Map title to eventName for frontend
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat(),
        'venue': event.location,  # Map location to venue for frontend
        'location': event.location,
        'poster': event.image_url,  # Map image_url to poster for frontend
        'image_url': event.image_url,
        'is_featured': event.is_featured,
        'clubName': event.club_name,  # Map club_name to clubName for frontend
        'club_name': event.club_name,
        'department': event.department,
        'timing': event.timing,
        'registrationType': event.registration_type,  # Map for frontend
        'registration_type': event.registration_type,
        'eventList': event.event_list,
        'event_list': event.event_list,
        'contact': event.contact,
        'eventType': event.event_type,  # Map for frontend
        'event_type': event.event_type,
        'created_by': event.created_by,
        'created_at': event.created_at.isoformat() if event.created_at else None
    } for event in events])

# Admin endpoint to get all events (including pending)
@app.route('/api/admin/events', methods=['GET'])
@admin_required
def get_all_events_admin():
    events = Event.query.order_by(Event.created_at.desc()).all()
    return jsonify([{
        'id': event.id,
        'eventName': event.title,
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat(),
        'venue': event.location,
        'location': event.location,
        'poster': event.image_url,
        'image_url': event.image_url,
        'is_featured': event.is_featured,
        'clubName': event.club_name,
        'club_name': event.club_name,
        'department': event.department,
        'timing': event.timing,
        'registrationType': event.registration_type,
        'registration_type': event.registration_type,
        'eventList': event.event_list,
        'event_list': event.event_list,
        'contact': event.contact,
        'eventType': event.event_type,
        'event_type': event.event_type,
        'created_by': event.created_by,
        'created_at': event.created_at.isoformat() if event.created_at else None,
        'approved': event.approved,
        'approved_by': event.approved_by,
        'approved_at': event.approved_at.isoformat() if event.approved_at else None
    } for event in events])

# Admin endpoint to approve/reject events
@app.route('/api/admin/events/<int:event_id>/approve', methods=['POST'])
@admin_required
def approve_event(event_id):
    event = Event.query.get_or_404(event_id)
    data = request.get_json() or {}
    payment_data = data.get('payment')
    approved = data.get('approved', True)
    
    # Get admin user from token
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    admin_id = payload.get('id')
    
    event.approved = approved
    event.approved_by = admin_id
    event.approved_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({
        'message': f'Event {"approved" if approved else "rejected"} successfully',
        'event_id': event_id,
        'approved': approved
    })

@app.route('/api/events/featured', methods=['GET'])
def get_featured_events():
    events = Event.query.filter_by(is_featured=True, approved=True).all()
    return jsonify([{
        'id': event.id,
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat(),
        'location': event.location,
        'image_url': event.image_url
    } for event in events])

# Event Categories Management
@app.route('/api/categories', methods=['GET'])
def get_categories():
    categories = EventCategory.query.all()
    return jsonify([{
        'id': cat.id,
        'name': cat.name,
        'description': cat.description,
        'color': cat.color,
        'event_count': Event.query.filter_by(category=cat.name, approved=True).count()
    } for cat in categories])

@app.route('/api/categories', methods=['POST'])
@admin_required
def create_category():
    data = request.get_json() or {}
    payment_data = data.get('payment')
    name = data.get('name')
    description = data.get('description', '')
    color = data.get('color', '#6b21a8')
    
    if not name:
        return jsonify({'message': 'Category name is required'}), 400
    
    # Check if category already exists
    if EventCategory.query.filter_by(name=name).first():
        return jsonify({'message': 'Category already exists'}), 400
    
    category = EventCategory(name=name, description=description, color=color)
    db.session.add(category)
    db.session.commit()
    
    return jsonify({
        'message': 'Category created successfully',
        'category': {
            'id': category.id,
            'name': category.name,
            'description': category.description,
            'color': category.color
        }
    }), 201

@app.route('/api/events/by-category/<category_name>', methods=['GET'])
def get_events_by_category(category_name):
    events = Event.query.filter_by(category=category_name, approved=True).all()
    return jsonify([{
        'id': event.id,
        'eventName': event.title,
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat(),
        'venue': event.location,
        'poster': event.image_url,
        'category': event.category,
        'clubName': event.club_name,
        'eventType': event.event_type,
        'max_capacity': event.max_capacity,
        'registration_fee': event.registration_fee,
        'total_registrations': EventRegistration.query.filter_by(event_id=event.id, status='registered').count()
    } for event in events])

# Event Templates System
@app.route('/api/templates', methods=['GET'])
@organizer_required
def get_event_templates():
    # Get user's templates and public templates
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    
    templates = EventTemplate.query.filter(
        (EventTemplate.created_by == current_user_id) | (EventTemplate.is_public == True)
    ).all()
    
    return jsonify([{
        'id': template.id,
        'name': template.name,
        'description': template.description,
        'is_public': template.is_public,
        'created_by': template.creator.username,
        'template_data': json.loads(template.template_data) if template.template_data else {}
    } for template in templates])

@app.route('/api/templates', methods=['POST'])
@organizer_required
def create_event_template():
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    
    data = request.get_json() or {}
    payment_data = data.get('payment')
    template = EventTemplate(
        name=data.get('name'),
        description=data.get('description', ''),
        template_data=json.dumps(data.get('template_data', {})),
        created_by=current_user_id,
        is_public=data.get('is_public', False)
    )
    
    db.session.add(template)
    db.session.commit()
    
    return jsonify({
        'message': 'Template created successfully',
        'template': {
            'id': template.id,
            'name': template.name,
            'description': template.description
        }
    }), 201

# Advanced Event Search and Filtering
@app.route('/api/events/search', methods=['GET'])
def search_events():
    query = request.args.get('q', '')
    category = request.args.get('category', '')
    event_type = request.args.get('type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    min_fee = request.args.get('min_fee', '')
    max_fee = request.args.get('max_fee', '')
    
    # Start with approved events
    events_query = Event.query.filter_by(approved=True)
    
    # Apply filters
    if query:
        events_query = events_query.filter(
            Event.title.contains(query) |
            Event.description.contains(query) |
            Event.club_name.contains(query)
        )
    
    if category:
        events_query = events_query.filter_by(category=category)
    
    if event_type:
        events_query = events_query.filter_by(event_type=event_type)
    
    if date_from:
        date_from_obj = datetime.fromisoformat(date_from)
        events_query = events_query.filter(Event.date >= date_from_obj)
    
    if date_to:
        date_to_obj = datetime.fromisoformat(date_to)
        events_query = events_query.filter(Event.date <= date_to_obj)
    
    if min_fee:
        events_query = events_query.filter(Event.registration_fee >= float(min_fee))
    
    if max_fee:
        events_query = events_query.filter(Event.registration_fee <= float(max_fee))
    
    events = events_query.order_by(Event.date.asc()).all()
    
    return jsonify([{
        'id': event.id,
        'eventName': event.title,
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat(),
        'venue': event.location,
        'poster': event.image_url,
        'category': event.category,
        'eventType': event.event_type,
        'clubName': event.club_name,
        'registration_fee': event.registration_fee,
        'max_capacity': event.max_capacity,
        'total_registrations': EventRegistration.query.filter_by(event_id=event.id, status='registered').count()
    } for event in events])

# Notification System
@app.route('/api/notifications', methods=['GET'])
@jwt_required()
def get_user_notifications():
    current_user_id = get_jwt_identity()
    
    # Get query parameters for filtering
    limit = request.args.get('limit', 50, type=int)
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    
    query = Notification.query.filter_by(user_id=current_user_id)
    
    if unread_only:
        query = query.filter_by(read=False)
    
    notifications = query.order_by(Notification.created_at.desc()).limit(limit).all()
    
    return jsonify([{
        'id': notification.id,
        'type': notification.type,
        'title': notification.title,
        'message': notification.message,
        'read': notification.read,
        'created_at': notification.created_at.isoformat(),
        'event_id': notification.event_id,
        'event_title': notification.related_event.title if notification.related_event else None
    } for notification in notifications])

@app.route('/api/notifications/<int:notification_id>/mark-read', methods=['PUT'])
@jwt_required()
def mark_notification_read(notification_id):
    current_user_id = get_jwt_identity()
    
    notification = Notification.query.filter_by(
        id=notification_id, user_id=current_user_id
    ).first_or_404()
    
    notification.read = True
    db.session.commit()
    
    return jsonify({'message': 'Notification marked as read'})

@app.route('/api/notifications/mark-all-read', methods=['PUT'])
@jwt_required()
def mark_all_notifications_read():
    current_user_id = get_jwt_identity()
    
    Notification.query.filter_by(user_id=current_user_id, read=False).update(
        {Notification.read: True}
    )
    db.session.commit()
    
    return jsonify({'message': 'All notifications marked as read'})

@app.route('/api/notifications/send-event-update', methods=['POST'])
@organizer_required
def send_event_update():
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    is_admin = payload.get('is_admin', False)
    
    data = request.get_json()
    event_id = data.get('event_id')
    message = data.get('message')
    title = data.get('title', 'Event Update')
    
    event = Event.query.get_or_404(event_id)
    
    # Check if user is event creator or admin
    if not is_admin and event.created_by != current_user_id:
        return jsonify({'message': 'Not authorized'}), 403
    
    # Get all registered users for this event
    registrations = EventRegistration.query.filter_by(event_id=event_id).all()
    
    notifications_created = 0
    for registration in registrations:
        notification = Notification(
            user_id=registration.user_id,
            type='event_update',
            title=title,
            message=message,
            event_id=event_id
        )
        db.session.add(notification)
        notifications_created += 1
    
    db.session.commit()
    
    return jsonify({
        'message': f'Event update sent to {notifications_created} users',
        'notifications_sent': notifications_created
    })

# Email Notification Simulation (In production, integrate with email service)
def send_email_notification(user_email, subject, message):
    """Simulate sending email notification - integrate with actual email service in production"""
    print(f"ðŸ“§ EMAIL NOTIFICATION SENT")
    print(f"To: {user_email}")
    print(f"Subject: {subject}")
    print(f"Message: {message}")
    print("â”€" * 50)

# Event Registration Endpoints
@app.route('/api/events/<int:event_id>/register', methods=['POST', 'OPTIONS'])
@jwt_required()
def register_for_event(event_id):
    if request.method == 'OPTIONS':
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    current_user_id = get_jwt_identity()
    event = Event.query.get_or_404(event_id)
    
    # Check if event is approved and active
    if not event.approved:
        return jsonify({'message': 'Event is not approved yet'}), 400
    if event.status != 'active':
        return jsonify({'message': f'Event is {event.status}'}), 400
    
    # Check registration deadline
    if event.registration_deadline and datetime.utcnow() > event.registration_deadline:
        return jsonify({'message': 'Registration deadline has passed'}), 400
    
    # Check if already registered
    existing_registration = EventRegistration.query.filter_by(
        event_id=event_id, user_id=current_user_id
    ).first()
    if existing_registration:
        return jsonify({'message': 'Already registered for this event'}), 400
    
    data = request.get_json() or {}
    payment_data = data.get('payment')

    # Check capacity and determine status
    current_registrations = EventRegistration.query.filter_by(
        event_id=event_id, status='registered'
    ).count()
    
    registration_status = 'registered'
    if event.max_capacity > 0 and current_registrations >= event.max_capacity:
        if event.allow_waitlist:
            registration_status = 'waitlisted'
        else:
            return jsonify({'message': 'Event is full and waitlist is not allowed'}), 400
    
    # Create registration
    registration = EventRegistration(
        event_id=event_id,
        user_id=current_user_id,
        status=registration_status,
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        email=data.get('email', ''),
        phone=data.get('phone'),
        department=data.get('department'),
        college=data.get('college'),
        year=data.get('year'),
        participating_events=data.get('participating_events'),
        organization=data.get('organization'),
        dietary_requirements=data.get('dietary_requirements'),
        special_needs=data.get('special_needs')
    )
    
    # Handle approval requirement
    if event.requires_approval:
        registration.status = 'pending_approval'

    payment_required = (
        event.payment_enabled
        and (event.registration_fee or 0) > 0
        and registration.status == 'registered'
    )

    if payment_required:
        payment_details = payment_data or {}
        order_id = payment_details.get('order_id')
        payment_id = payment_details.get('payment_id')
        signature = payment_details.get('signature')

        if not all([order_id, payment_id, signature]):
            return jsonify({'message': 'Payment details are required to complete registration'}), 400

        # Check if this is a test mode payment (for demo purposes)
        is_test_payment = (
            str(order_id).startswith('order_test_') or
            str(payment_id).startswith('pay_test_')
        )

        if is_test_payment:
            # TEST MODE: Accept simulated payment without Razorpay verification
            registration.payment_status = 'completed'
            registration.payment_method = 'razorpay_test'
            registration.payment_reference = payment_id
        else:
            # PRODUCTION MODE: Verify with Razorpay
            key_id, key_secret = get_razorpay_credentials(event)
            if not key_id or not key_secret:
                return jsonify({'message': 'Payment gateway is not configured for this event'}), 500

            try:
                client = get_razorpay_client(key_id, key_secret)
                client.utility.verify_payment_signature({
                    'razorpay_order_id': order_id,
                    'razorpay_payment_id': payment_id,
                    'razorpay_signature': signature
                })

                expected_amount = int(round((event.registration_fee or 0) * 100))
                order_details = client.order.fetch(order_id)
                amount_paid = order_details.get('amount_paid') or 0
                if expected_amount > 0 and amount_paid < expected_amount:
                    return jsonify({'message': 'Payment verification failed: insufficient amount paid'}), 400

                registration.payment_status = 'completed'
                registration.payment_method = 'razorpay'
                registration.payment_reference = payment_id
            except SignatureVerificationError:
                return jsonify({'message': 'Payment verification failed. Please try again.'}), 400
            except RazorpayError as e:
                return jsonify({'message': f'Payment processing error: {str(e)}'}), 400
    else:
        if registration.status == 'registered':
            registration.payment_status = 'completed'
    
    try:
        db.session.add(registration)
        db.session.commit()
        
        # Create notification
        notification = Notification(
            user_id=current_user_id,
            type='registration',
            title=f'Registration {"submitted" if event.requires_approval else "confirmed"}',
            message=f'Your registration for "{event.title}" has been {"submitted for approval" if event.requires_approval else "confirmed"}.',
            event_id=event_id,
            registration_id=registration.id
        )
        db.session.add(notification)
        db.session.commit()
        
        return jsonify({
            'message': f'Successfully {"registered" if registration_status == "registered" else registration_status}',
            'registration': {
                'id': registration.id,
                'status': registration.status,
                'registered_at': registration.registered_at.isoformat()
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Registration failed: {str(e)}'}), 500


@app.route('/api/payments/create-order', methods=['POST'])
@jwt_required()
def create_payment_order():
    data = request.get_json() or {}
    event_id = data.get('event_id')
    if not event_id:
        return jsonify({'message': 'Event ID is required'}), 400

    event = Event.query.get_or_404(event_id)
    if not event.approved or event.status != 'active':
        return jsonify({'message': 'Event is not available for registration'}), 400

    amount = event.registration_fee or 0
    if not event.payment_enabled or amount <= 0:
        return jsonify({'message': 'Payments are not enabled for this event'}), 400

    amount_paise = int(round(amount * 100))
    if amount_paise <= 0:
        return jsonify({'message': 'Invalid payment amount configured for this event'}), 400

    key_id, key_secret = get_razorpay_credentials(event)
    if not key_id or not key_secret:
        return jsonify({'message': 'Payment gateway is not configured for this event'}), 500

    current_user_id = get_jwt_identity()
    currency = data.get('currency', 'INR')

    try:
        client = get_razorpay_client(key_id, key_secret)
        receipt = f"eventflow-{event_id}-{current_user_id}-{int(datetime.utcnow().timestamp())}"
        order = client.order.create({
            'amount': amount_paise,
            'currency': currency,
            'receipt': receipt,
            'payment_capture': 1,
            'notes': {
                'event_id': str(event_id),
                'user_id': str(current_user_id)
            }
        })

        return jsonify({
            'order_id': order.get('id'),
            'amount': order.get('amount'),
            'currency': order.get('currency'),
            'key_id': key_id,
            'event_id': event.id,
            'event_title': event.title
        })
    except RazorpayError as e:
        return jsonify({'message': f'Unable to initiate payment: {str(e)}'}), 400

@app.route('/api/events/<int:event_id>/registrations', methods=['GET'])
@organizer_required
def get_event_registrations(event_id):
    # Check if user is event creator or admin
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    is_admin = payload.get('is_admin', False)
    
    event = Event.query.get_or_404(event_id)
    if not is_admin and event.created_by != current_user_id:
        return jsonify({'message': 'Not authorized to view registrations'}), 403
    
    registrations = EventRegistration.query.filter_by(event_id=event_id).all()
    
    return jsonify({
        'event': {
            'id': event.id,
            'title': event.title,
            'max_capacity': event.max_capacity,
            'current_registrations': len([r for r in registrations if r.status == 'registered'])
        },
        'registrations': [{
            'id': reg.id,
            'user_id': reg.user_id,
            'first_name': reg.first_name,
            'last_name': reg.last_name,
            'email': reg.email,
            'phone': reg.phone,
            'organization': reg.organization,
            'status': reg.status,
            'registered_at': reg.registered_at.isoformat(),
            'payment_status': reg.payment_status,
            'check_in_time': reg.check_in_time.isoformat() if reg.check_in_time else None
        } for reg in registrations]
    })

@app.route('/api/registrations/<int:registration_id>/status', methods=['PUT'])
@organizer_required
def update_registration_status(registration_id):
    registration = EventRegistration.query.get_or_404(registration_id)
    event = registration.event
    
    # Check if user is event creator or admin
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    is_admin = payload.get('is_admin', False)
    
    if not is_admin and event.created_by != current_user_id:
        return jsonify({'message': 'Not authorized'}), 403
    
    data = request.get_json()
    new_status = data.get('status')
    
    if new_status not in ['registered', 'waitlisted', 'cancelled', 'attended', 'pending_approval']:
        return jsonify({'message': 'Invalid status'}), 400
    
    old_status = registration.status
    registration.status = new_status
    
    if new_status in ['registered', 'cancelled'] and old_status == 'pending_approval':
        registration.approved_by = current_user_id
        registration.approved_at = datetime.utcnow()
    
    try:
        db.session.commit()
        
        # Create notification for user
        notification = Notification(
            user_id=registration.user_id,
            type='approval' if new_status == 'registered' else 'status_change',
            title=f'Registration {new_status.replace("_", " ").title()}',
            message=f'Your registration for "{event.title}" has been {new_status.replace("_", " ")}.',
            event_id=event.id,
            registration_id=registration_id
        )
        db.session.add(notification)
        db.session.commit()
        
        return jsonify({
            'message': 'Registration status updated',
            'registration': {
                'id': registration.id,
                'status': registration.status
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Update failed: {str(e)}'}), 500

@app.route('/api/registrations/<int:registration_id>/checkin', methods=['POST'])
@organizer_required
def checkin_attendee(registration_id):
    registration = EventRegistration.query.get_or_404(registration_id)
    event = registration.event
    
    # Check authorization
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(' ')[1]
    payload = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
    current_user_id = payload.get('id')
    is_admin = payload.get('is_admin', False)
    
    if not is_admin and event.created_by != current_user_id:
        return jsonify({'message': 'Not authorized'}), 403
    
    if registration.check_in_time:
        return jsonify({'message': 'Already checked in'}), 400
    
    registration.check_in_time = datetime.utcnow()
    registration.check_in_by = current_user_id
    registration.status = 'attended'
    
    try:
        db.session.commit()
        return jsonify({
            'message': 'Successfully checked in',
            'check_in_time': registration.check_in_time.isoformat()
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Check-in failed: {str(e)}'}), 500

@app.route('/api/my-events', methods=['GET'])
@jwt_required()
def get_my_events():
    current_user_id = get_jwt_identity()
    registrations = EventRegistration.query.filter_by(user_id=current_user_id).all()

    return jsonify([{
        'id': reg.id,
        'eventName': reg.event.title,
        'title': reg.event.title,
        'description': reg.event.description,
        'date': reg.event.date.isoformat(),
        'venue': reg.event.location,
        'location': reg.event.location,
        'poster': reg.event.image_url,
        'image_url': reg.event.image_url,
        'status': reg.status,
        'registered_at': reg.registered_at.isoformat(),
        'payment_status': reg.payment_status,
        'check_in_time': reg.check_in_time.isoformat() if reg.check_in_time else None
    } for reg in registrations])

@app.route('/api/my-registrations', methods=['GET'])
@jwt_required()
def get_my_registrations():
    current_user_id = get_jwt_identity()
    registrations = EventRegistration.query.filter_by(user_id=current_user_id).all()

    return jsonify([{
        'id': reg.id,
        'event': {
            'id': reg.event.id,
            'title': reg.event.title,
            'date': reg.event.date.isoformat(),
            'location': reg.event.location,
            'image_url': reg.event.image_url
        },
        'status': reg.status,
        'registered_at': reg.registered_at.isoformat(),
        'payment_status': reg.payment_status,
        'check_in_time': reg.check_in_time.isoformat() if reg.check_in_time else None
    } for reg in registrations])

@app.route('/api/events/<int:event_id>', methods=['GET'])
def get_event_details(event_id):
    event = Event.query.get_or_404(event_id)
    
    # Check if user is admin - if so, allow access to any event (even unapproved)
    is_admin_user = False
    current_user_id = None
    try:
        from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
        verify_jwt_in_request(optional=True)
        current_user_id = get_jwt_identity()
        if current_user_id:
            user = User.query.get(current_user_id)
            if user and user.is_admin:
                is_admin_user = True
    except:
        pass  # User not authenticated
    
    # Only block unapproved events for non-admin users
    if not event.approved and not is_admin_user:
        return jsonify({'message': 'Event not found or not approved'}), 404
    
    # Get registration stats
    total_registrations = EventRegistration.query.filter_by(event_id=event_id, status='registered').count()
    waitlisted_count = EventRegistration.query.filter_by(event_id=event_id, status='waitlisted').count()
    
    # Check if current user is registered (if authenticated)
    user_registration = None
    if current_user_id:
        user_registration = EventRegistration.query.filter_by(
            event_id=event_id, user_id=current_user_id
        ).first()
    
    # Calculate availability
    is_full = event.max_capacity > 0 and total_registrations >= event.max_capacity
    can_register = not is_full or event.allow_waitlist
    registration_closed = event.registration_deadline and datetime.utcnow() > event.registration_deadline
    
    return jsonify({
        'id': event.id,
        'eventName': event.title,
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat(),
        'end_date': event.end_date.isoformat() if event.end_date else None,
        'venue': event.location,
        'location': event.location,
        'poster': event.image_url,
        'image_url': event.image_url,
        'is_featured': event.is_featured,
        'clubName': event.club_name,
        'club_name': event.club_name,
        'department': event.department,
        'timing': event.timing,
        'registrationType': event.registration_type,
        'registration_type': event.registration_type,
        'eventList': event.event_list,
        'event_list': event.event_list,
        'contact': event.contact,
        'eventType': event.event_type,
        'event_type': event.event_type,
        'category': event.category,
        'tags': event.tags,
        'status': event.status,
        'created_at': event.created_at.isoformat(),
        'updated_at': event.updated_at.isoformat(),
        
        # Registration info
        'max_capacity': event.max_capacity,
        'registration_deadline': event.registration_deadline.isoformat() if event.registration_deadline else None,
        'registration_fee': event.registration_fee,
        'payment_enabled': event.payment_enabled,
        'requires_approval': event.requires_approval,
        'allow_waitlist': event.allow_waitlist,
        'external_registration_url': event.external_registration_url,
        
        # Current registration stats
        'total_registrations': total_registrations,
        'waitlisted_count': waitlisted_count,
        'available_spots': max(0, event.max_capacity - total_registrations) if event.max_capacity > 0 else None,
        'is_full': is_full,
        'can_register': can_register and not registration_closed,
        'registration_closed': registration_closed,
        
        # User's registration status
        'user_registration': {
            'id': user_registration.id,
            'status': user_registration.status,
            'registered_at': user_registration.registered_at.isoformat()
        } if user_registration else None
    })


@app.route('/api/events/<int:event_id>', methods=['PUT'])
@admin_required
def update_event(event_id):
    """Update an existing event. Admin only.

    Accepts multipart/form-data (same fields as create_event). If a poster/image
    file is provided it will replace the existing image.
    """
    event = Event.query.get_or_404(event_id)

    # Handle file upload if present
    file = request.files.get('poster') or request.files.get('image')
    if file and getattr(file, 'filename', '') != '':
        if not allowed_file(file.filename):
            return jsonify({'message': 'File type not allowed. Allowed types are: png, jpg, jpeg, gif'}), 400

        filename = secure_filename(file.filename)
        unique_filename = f"{int(datetime.now().timestamp())}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        file.save(filepath)
        event.image_url = f'/uploads/{unique_filename}'

    # Update simple fields if provided
    title = request.form.get('eventName') or request.form.get('title')
    if title:
        event.title = title

    description = request.form.get('description')
    if description is not None:
        event.description = description

    location = request.form.get('venue') or request.form.get('location')
    if location:
        event.location = location

    date_str = request.form.get('date')
    if date_str:
        try:
            try:
                event.date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                event.date = datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError) as e:
            return jsonify({'message': f'Invalid date format. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM. Error: {str(e)}'}), 400

    # Other optional fields
    club_name = request.form.get('clubName')
    if club_name is not None:
        event.club_name = club_name

    department = request.form.get('department')
    if department is not None:
        event.department = department

    timing = request.form.get('timing')
    if timing is not None:
        event.timing = timing

    registration_type = request.form.get('registrationType')
    if registration_type is not None:
        event.registration_type = registration_type

    event_list = request.form.get('eventList')
    if event_list is not None:
        event.event_list = event_list

    contact = request.form.get('contact')
    if contact is not None:
        event.contact = contact

    event_type = request.form.get('eventType')
    if event_type is not None:
        event.event_type = event_type

    # Numeric/boolean fields
    max_capacity = request.form.get('max_capacity')
    if max_capacity is not None:
        try:
            event.max_capacity = int(max_capacity)
        except ValueError:
            return jsonify({'message': 'Invalid max_capacity value'}), 400

    registration_fee = request.form.get('registration_fee')
    if registration_fee is not None and registration_fee != '':
        try:
            event.registration_fee = float(registration_fee)
        except ValueError:
            return jsonify({'message': 'Invalid registration_fee value'}), 400

    payment_enabled = request.form.get('payment_enabled')
    if payment_enabled is not None:
        event.payment_enabled = str(payment_enabled).lower() in ['true', '1', 'on']

    razorpay_key_id = request.form.get('razorpay_key_id')
    if razorpay_key_id is not None:
        event.razorpay_key_id = razorpay_key_id

    razorpay_key_secret = request.form.get('razorpay_key_secret')
    if razorpay_key_secret is not None:
        event.razorpay_key_secret = razorpay_key_secret

    organizer_account_id = request.form.get('organizer_account_id')
    if organizer_account_id is not None:
        event.organizer_account_id = organizer_account_id

    # Optionally update status/approved flags if provided (admin only)
    approved = request.form.get('approved')
    if approved is not None:
        event.approved = str(approved).lower() in ['true', '1', 'on']

    status = request.form.get('status')
    if status is not None:
        event.status = status

    # Commit changes
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Failed to update event: {str(e)}'}), 500

    return jsonify({'message': 'Event updated successfully', 'event': {
        'id': event.id,
        'title': event.title,
        'description': event.description,
        'date': event.date.isoformat() if event.date else None,
        'location': event.location,
        'image_url': event.image_url,
        'is_featured': event.is_featured,
        'club_name': event.club_name,
        'department': event.department,
        'timing': event.timing,
        'registration_type': event.registration_type,
        'event_list': event.event_list,
        'contact': event.contact,
        'event_type': event.event_type
    }}), 200

# Database initialization moved to init_database() function to prevent data loss

# User Authentication Routes
@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        # Handle preflight request
        response = jsonify({'message': 'OK'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,Accept')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
        
    data = request.get_json()
    identifier = data.get('identifier')  # Can be email or username
    password = data.get('password')
    
    if not all([identifier, password]):
        response = jsonify({'message': 'Missing email/username or password'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
