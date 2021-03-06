'''
The API backend
'''

from datetime import datetime
from datetime import timedelta
from eastern_tzinfo import Eastern_tzinfo
import json
import os
from django.utils.http import urlquote
import Crypto.Random
from Crypto.Protocol import KDF
import jwt

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import urlfetch
from google.appengine.ext import ndb

from models import Profile
from models import ProfileMsg
from models import AccountAuthMsg
from models import ChangePasswordMsg
from models import Match
from models import MatchMsg
from models import MatchesMsg
from models import AccessTokenMsg
from models import StringMsg
from models import BooleanMsg
from models import StringArrayMsg

# Custom accounts
from settings import CA_SECRET
from settings import EMAIL_VERIF_SECRET
# Facebook
from settings import FB_APP_ID
from settings import FB_APP_SECRET
from settings import FB_API_VERSION
# Google
from settings import GRECAPTCHA_SECRET
#from settings import WEB_CLIENT_ID
# SparkPost
from settings import SPARKPOST_SECRET

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api( name='tennis',
				version='v1',
				allowed_client_ids=[API_EXPLORER_CLIENT_ID],
				scopes=[EMAIL_SCOPE])
class TennisApi(remote.Service):
	"""Tennis API"""

	###################################################################
	# Email Management
	###################################################################

	def _postToSparkpost(self, payload):
		""" Post to Sparkpost API. Return True/False status """
		payload_json = json.dumps(payload)
		headers = {
			'Authorization': SPARKPOST_SECRET,
			'Content-Type': 'application/json',
		}

		url = 'https://api.sparkpost.com/api/v1/transmissions?num_rcpt_errors=3'
		try:
			result = urlfetch.Fetch(url, headers=headers, payload=payload_json, method=2)
		except:
			raise endpoints.BadRequestException('urlfetch error: Unable to POST to SparkPost')
			return False
		data = json.loads(result.content)

		# Determine status from SparkPost, return True/False
		if 'errors' in data:
			return False
		if data['results']['total_accepted_recipients'] != 1:
			return False

		return True

	def _emailVerif(self, profile):
		""" Send verification email, given reference to Profile object. Return success True/False. """
		# Generate JWT w/ payload of userId and email, secret is EMAIL_VERIF_SECRET
		token = jwt.encode(
			{'userId': profile.userId, 'contactEmail': profile.contactEmail},
			EMAIL_VERIF_SECRET,
			algorithm='HS256'
		)

		# Create SparkPost request to send verification email
		payload = {
			'recipients': [{
				'address': {
					'email': profile.contactEmail,
					'name': profile.firstName + ' ' + profile.lastName,
				},
				'substitution_data': {
					'first_name': profile.firstName,
					'token':      token,
				},
			}],
			'content': {
				'template_id': 'email-verif',
			},
		}

		return self._postToSparkpost(payload)

	def _emailPwChange(self, profile):
		""" Send password change notification email. Return success True/False. """
		# If user email is unverified, return
		if not profile.emailVerified:
			return False

		# Create SparkPost request to send pw change notification email
		payload = {
			'recipients': [{
				'address': {
					'email': profile.contactEmail,
					'name': profile.firstName + ' ' + profile.lastName,
				},
				'substitution_data': {
					'first_name': profile.firstName,
				},
			}],
			'content': {
				'template_id': 'password-change-notification',
			},
		}

		return self._postToSparkpost(payload)

	def _emailPwReset(self, profile):
		""" Send password reset link to user's email. Return success True/False. """
		# If user email is unverified, return
		if not profile.emailVerified:
			return False

		# Generate JWT to reset password, expires 30 minutes from now
		token = jwt.encode({'userId': profile.userId, 'exp': datetime.now() + timedelta(minutes=30)}, CA_SECRET, algorithm='HS256')

		# Create SparkPost request to send pw reset email
		payload = {
			'recipients': [{
				'address': {
					'email': profile.contactEmail,
					'name': profile.firstName + ' ' + profile.lastName,
				},
				'substitution_data': {
					'first_name': profile.firstName,
					'token': token
				},
			}],
			'content': {
				'template_id': 'password-reset',
			},
		}

		return self._postToSparkpost(payload)

	def _emailMatchUpdate(self, user_id, message, person, action):
		"""
		Send match update email to user, via match-update SparkPost template
		Given user, message content, person-of-interest, action (e.g. joined/left)
		"""
		# Get profile of user_id
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# If user disabled email notifications or email is unverified, return
		if not profile.notifications[1] or not profile.emailVerified:
			return False

		# Create SparkPost request to send notification email
		payload = {
			'recipients': [{
				'address': {
					'email': profile.contactEmail,
					'name': profile.firstName + ' ' + profile.lastName,
				},
				'substitution_data': {
					'first_name': profile.firstName,
					'message':    message,
					'person':     person,
					'action':     action,
				},
			}],
			'content': {
				'template_id': 'match-update',
			},
		}

		return self._postToSparkpost(payload)

	def _emailAvailMatch(self, partner, message, player_name):
		"""
		Send notification to potential parter of a newly created match
		'partner' is the person to send the email to, a Profile object
		'player_name' is the name of the person who created the match, a string
		"""
		profile = partner

		# If user disabled email notifications or email is unverified, return
		if not profile.notifications[1] or not profile.emailVerified:
			return False

		# Create SparkPost request to send notification email
		payload = {
			'recipients': [{
				'address': {
					'email': profile.contactEmail,
					'name': profile.firstName + ' ' + profile.lastName,
				},
				'substitution_data': {
					'first_name': profile.firstName,
					'message':    message,
					'person':     player_name,
				},
			}],
			'content': {
				'template_id': 'available-match-notification',
			},
		}

		return self._postToSparkpost(payload)


	@endpoints.method(AccessTokenMsg, StringMsg, path='',
		http_method='POST', name='verifyEmailToken')
	def verifyEmailToken(self, request):
		""" Verify email token, to verify email address. Return email address string or 'error' """
		status = StringMsg()  # return status
		status.data = 'error'  # default to error

		# Decode the JWT token
		try:
			payload = jwt.decode(request.accessToken, EMAIL_VERIF_SECRET, algorithm='HS256')
		except:
			return status

		# If valid JWT token, extract the info and update DB if applicable
		user_id = payload['userId']
		email = payload['contactEmail']

		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# If user changed email and clicked on old email verif link, this request is invalid
		if profile.contactEmail != email:
			return status

		# If we get here then email is verified. Update DB and return successful status
		profile.emailVerified = True
		profile.put()

		status.data = email
		return status


	###################################################################
	# Custom Accounts
	###################################################################

	def _genToken(self, payload):
		""" Generate custom auth JWT token given payload dict """
		secret = CA_SECRET
		return jwt.encode(payload, secret, algorithm='HS256')

	def _decodeToken(self, token):
		""" Decode custom token. If successful, return payload. Else, return None. """
		secret = CA_SECRET
		try:
			return jwt.decode(token, secret, algorithm='HS256')
		except:
			return None

	def _getUserId(self, token):
		""" Get userId: First check if local account, then check if FB account """
		# See if token belongs to custom account user
		ca_payload = self._decodeToken(token)
		if ca_payload is not None:
			return ca_payload['userId']

		# If above failed, try FB token
		return self._getFbUserId(token)


	@endpoints.method(AccessTokenMsg, BooleanMsg, path='',
		http_method='POST', name='verifyToken')
	def verifyToken(self, request):
		""" Verify validity of custom account token, check if user is logged in. Return True/False. """
		status = BooleanMsg()  # return status
		status.data = False  # default to invalid (False)

		ca_payload = self._decodeToken(request.accessToken)
		if ca_payload is not None:
			if 'userId' in ca_payload and 'session_id' in ca_payload:
				# Check if user is logged into valid session
				user_id = ca_payload['userId']
				session_id = ca_payload['session_id']

				profile_key = ndb.Key(Profile, user_id)
				profile = profile_key.get()
				if profile is not None:
					status.data = profile.loggedIn and (profile.session_id == session_id)

		return status

	@endpoints.method(AccountAuthMsg, StringMsg, path='',
		http_method='POST', name='createAccount')
	def createAccount(self, request):
		""" Create new custom account """
		status = StringMsg()  # return status
		status.data = 'error'  # default to error

		# Verify if user passed reCAPTCHA
		# POST request to Google reCAPTCHA API
		url = 'https://www.google.com/recaptcha/api/siteverify?secret=%s&response=%s' % (GRECAPTCHA_SECRET, request.recaptcha)
		try:
			result = urlfetch.Fetch(url, method=2)
		except:
			raise endpoints.BadRequestException('urlfetch error: Unable to POST to Google reCAPTCHA')
			return status
		data = json.loads(result.content)
		if not data['success']:
			status.data = 'recaptcha_fail'
			return status

		user_id = 'ca_' + request.email

		# Get profile from datastore -- if profile not found, then profile=None
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# If profile exists, return status
		if profile:
			status.data = 'user_exists'
			return status

		# Salt and hash the password
		salt = Crypto.Random.new().read(16)
		passkey = KDF.PBKDF2(request.password, salt).encode('hex')

		salt_passkey = salt.encode('hex') + '|' + passkey

		# Generate new session ID
		session_id = Crypto.Random.new().read(16).encode('hex')

		# Create new profile for user
		Profile(
			key = profile_key,
			userId = user_id,
			contactEmail = request.email,
			salt_passkey = salt_passkey,
			session_id = session_id,
			loggedIn = True,
			emailVerified = False,
			notifications = [False, True]
		).put()

		# Generate user access token
		token = self._genToken({'userId': user_id, 'session_id': session_id})

		# If we get here, means we suceeded
		status.data = 'success'
		status.accessToken = token
		return status

	@endpoints.method(AccountAuthMsg, StringMsg, path='',
		http_method='POST', name='login')
	def login(self, request):
		""" Check username/password to login """
		status = StringMsg()  # return status
		status.data = 'error'  # default to error

		# Verify if user passed reCAPTCHA
		# POST request to Google reCAPTCHA API
		url = 'https://www.google.com/recaptcha/api/siteverify?secret=%s&response=%s' % (GRECAPTCHA_SECRET, request.recaptcha)
		try:
			result = urlfetch.Fetch(url, method=2)
		except:
			raise endpoints.BadRequestException('urlfetch error: Unable to POST to Google reCAPTCHA')
			return status
		data = json.loads(result.content)
		if not data['success']:
			status.data = 'recaptcha_fail'
			return status

		user_id = 'ca_' + request.email

		# Get profile from datastore -- if profile not found, then profile=None
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# If profile does not exist, return False
		if not profile:
			return status

		# Parse salt and passkey from DB, compare it to provided version
		db_salt, db_passkey = profile.salt_passkey.split('|')
		passkey = KDF.PBKDF2(request.password, db_salt.decode('hex')).encode('hex')

		# Passwords don't match, return False
		if passkey != db_passkey:
			return status

		# Generate new session ID
		session_id = Crypto.Random.new().read(16).encode('hex')
		profile.session_id = session_id

		# Update user's status to logged-in
		profile.loggedIn = True
		profile.put()

		# Generate user access token
		token = self._genToken({'userId': user_id, 'session_id': session_id})

		# If we get here, means we suceeded
		status.data = 'success'
		status.accessToken = token
		return status

	@endpoints.method(AccessTokenMsg, BooleanMsg, path='',
		http_method='POST', name='logout')
	def logout(self, request):
		""" Logout """
		status = BooleanMsg()  # return status
		status.data = False  # default to error (False)

		user_id = self._getUserId(request.accessToken)

		# Get Profile from NDB, update login status
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()
		profile.session_id = 'invalid'
		profile.loggedIn = False
		profile.put()

		status.data = True
		return status

	@endpoints.method(ChangePasswordMsg, StringMsg, path='',
		http_method='POST', name='changePassword')
	def changePassword(self, request):
		""" Change password """
		status = StringMsg()
		status.data = 'error'

		# Get user profile
		user_id = self._getUserId(request.accessToken)
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# Not sure how this would happen, but it would be an error
		if not profile:
			return status

		# Check if provided old password matches user's current password
		db_salt, db_passkey = profile.salt_passkey.split('|')
		passkey = KDF.PBKDF2(request.oldPw, db_salt.decode('hex')).encode('hex')

		# Passwords don't match, return
		if passkey != db_passkey:
			status.data = 'old_pw_wrong'
			return status

		# If passwords match, salt & hash new password
		new_salt = Crypto.Random.new().read(16)
		new_passkey = KDF.PBKDF2(request.newPw, new_salt).encode('hex')
		new_salt_passkey = new_salt.encode('hex') + '|' + new_passkey
		profile.salt_passkey = new_salt_passkey

		# Also generate new session ID
		session_id = Crypto.Random.new().read(16).encode('hex')
		profile.session_id = session_id

		# Update DB
		profile.put()

		# Send user an email to notify password change
		self._emailPwChange(profile)

		# Return success status
		status.data = 'success'
		return status

	@endpoints.method(AccountAuthMsg, StringMsg, path='',
		http_method='POST', name='forgotPassword')
	def forgotPassword(self, request):
		""" Forgot password, send user password reset link via email """
		status = StringMsg()
		status.data = 'error'

		# Verify if user passed reCAPTCHA
		# POST request to Google reCAPTCHA API
		url = 'https://www.google.com/recaptcha/api/siteverify?secret=%s&response=%s' % (GRECAPTCHA_SECRET, request.recaptcha)
		try:
			result = urlfetch.Fetch(url, method=2)
		except:
			raise endpoints.BadRequestException('urlfetch error: Unable to POST to Google reCAPTCHA')
			return status
		data = json.loads(result.content)
		if not data['success']:
			status.data = 'recaptcha_fail'
			return status

		user_id = 'ca_' + request.email

		# Get profile from datastore -- if profile not found, then profile=None
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# Check if profile exists. If not, return status
		if not profile:
			status.data = 'invalid_email'
			return status

		# If email unverified, return status
		if not profile.emailVerified:
			status.data = 'unverified_email'
			return status

		# Send password reset link to user's email
		if self._emailPwReset(profile):
			status.data = 'success'

		return status

	@endpoints.method(StringMsg, StringMsg, path='',
		http_method='POST', name='resetPassword')
	def resetPassword(self, request):
		""" Reset password, verify token. Return status. """
		status = StringMsg()
		status.data = 'error'

		# Validate and decode token
		try:
			payload = jwt.decode(request.accessToken, CA_SECRET, algorithm='HS256')
		except:
			status.data = 'invalid_token'
			return status

		# Get user profile
		user_id = payload['userId']
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# Not sure how this would happen, but it would be an error
		if not profile:
			return status

		# Salt & hash new password
		new_salt = Crypto.Random.new().read(16)
		new_passkey = KDF.PBKDF2(request.data, new_salt).encode('hex')
		new_salt_passkey = new_salt.encode('hex') + '|' + new_passkey
		profile.salt_passkey = new_salt_passkey

		# Also generate new session ID
		session_id = Crypto.Random.new().read(16).encode('hex')
		profile.session_id = session_id

		# Update DB
		profile.put()

		# Send user an email to notify password change
		self._emailPwChange(profile)

		# Return success status
		status.data = 'success'
		return status


	###################################################################
	# Facebook Authentication & Graph API
	###################################################################

	def _getFbUserId(self, token):
		""" Given token, find FB user ID from FB, and return it """
		url = 'https://graph.facebook.com/v%s/me?access_token=%s&fields=id' % (FB_API_VERSION, token)
		try:
			result = urlfetch.Fetch(url, method=1)
		except:
			raise endpoints.BadRequestException('urlfetch error: Get FB user ID')
			return None

		data = json.loads(result.content)
		if 'error' in data:
			raise endpoints.BadRequestException('FB OAuth token error')
			return None

		user_id = 'fb_' + data['id']
		return user_id

	def _postFbNotif(self, user_id, message, href):
		"""
		Post FB notification with message to user
		"""
		# Get profile of user_id
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# Only post FB notif if FB user and user enabled FB notifs
		if not (user_id[:3] == 'fb_' and profile.notifications[0]):
			return False

		fb_user_id = user_id[3:]

		# Get App Access Token, different than User Token
		# https://developers.facebook.com/docs/facebook-login/access-tokens/#apptokens
		url = 'https://graph.facebook.com/v%s/oauth/access_token?grant_type=client_credentials&client_id=%s&client_secret=%s' % (FB_API_VERSION, FB_APP_ID, FB_APP_SECRET)
		try:
			result = urlfetch.Fetch(url, method=1)
		except:
			raise endpoints.BadRequestException('urlfetch error: FB app access token')
			return False

		token = json.loads(result.content)['access_token']

		url = 'https://graph.facebook.com/v%s/%s/notifications?access_token=%s&template=%s&href=%s' % (FB_API_VERSION, fb_user_id, token, message, href)
		try:
			result = urlfetch.Fetch(url, method=2)
		except:
			raise endpoints.BadRequestException('urlfetch error: Unable to POST FB notification')
			return False

		data = json.loads(result.content)
		if 'error' in data:
			raise endpoints.BadRequestException('FB notification error')
			return False

		return True


	@endpoints.method(AccessTokenMsg, StringMsg, path='',
		http_method='POST', name='fbLogin')
	def fbLogin(self, request):
		""" Handle Facebook login """
		status = StringMsg()  # return status message
		status.data = 'error'  # default to error message, unless specified otherwise
		'''
		# Swap short-lived token for long-lived token
		short_token = request.data

		url = 'https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id=%s&client_secret=%s&fb_exchange_token=%s' % (
			FB_APP_ID, FB_APP_SECRET, short_token)
		try:
			result = urlfetch.Fetch(url, method=1)
		except:
			print('urlfetch error1')
			return status

		token = result.content.split('&')[0]  # 'access_token=blahblahblah'
		'''
		token = request.accessToken

		# Use token to get user info from API
		url = 'https://graph.facebook.com/v%s/me?access_token=%s&fields=name,id,email' % (FB_API_VERSION, token)
		try:
			result = urlfetch.Fetch(url, method=1)
		except:
			raise endpoints.BadRequestException('urlfetch error')
			return status

		data = json.loads(result.content)

		if 'error' in data:
			raise endpoints.BadRequestException('FB OAuth token error')
			return status

		user_id = 'fb_' + data['id']
		first_name = data['name'].split()[0]
		last_name = data['name'].split()[-1]
		email = data['email']

		# Get existing profile from datastore, or create new one
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# If profile already exists, return 'existing_user'
		# Unless, they have empty firstName (maybe they got d/c'ed on profile page)
		if profile:
			# If empty first name, return new_user
			if profile.firstName == '':
				status.data = 'new_user'
				return status

			# If user previously logged-out, update login status in NDB
			if not profile.loggedIn:
				profile.loggedIn = True
				profile.put()

			status.data = 'existing_user'
			return status

		# Else, create new profile and return 'new_user'
		profile = Profile(
			key = profile_key,
			userId = user_id,
			contactEmail = email,
			firstName = first_name,
			lastName = last_name,
			loggedIn = True,
			emailVerified = False,
			notifications = [True, False]
		).put()

		status.data = 'new_user'
		return status


	###################################################################
	# Profile Objects
	###################################################################

	@ndb.transactional(xg=True)
	def _updateProfile(self, request):
		"""Update user profile."""
		status = StringMsg()
		status.data = 'normal'

		token = request.accessToken
		user_id = self._getUserId(token)

		# Make sure the incoming message is initialized, raise exception if not
		request.check_initialized()

		# Get existing profile from datastore
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# Check if email changed. Note custom account users cannot change email.
		email_change = (profile.contactEmail != request.contactEmail) and (user_id[:3] != 'ca_')

		# Update profile object from the user's form
		for field in request.all_fields():
			if field.name == 'userId':
				continue  # userId is fixed
			elif user_id[:3] == 'ca_' and field.name == 'contactEmail':
				continue  # custom account users cannot change email address
			elif field.name != 'accessToken':
				setattr(profile, field.name, getattr(request, field.name))

		# If this is user's first time updating profile, or changing email address
		# then send email verification
		if profile.pristine or email_change:
			profile.pristine = False
			self._emailVerif(profile)

			status.data = 'email_verif'

		# Save updated profile to datastore
		profile.put()

		return status


	@endpoints.method(AccessTokenMsg, ProfileMsg,
			path='', http_method='POST', name='getProfile')
	def getProfile(self, request):
		"""Return user profile."""
		token = request.accessToken
		user_id = self._getUserId(token)

		# Create new ndb key based on unique user ID (email)
		# Get profile from datastore -- if profile not found, then profile=None
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# If profile does not exist, return empty ProfileMsg
		if not profile:
			return ProfileMsg()

		# Else, copy profile to ProfileForm, and return it
		pf = ProfileMsg()
		for field in pf.all_fields():
			if hasattr(profile, field.name):
				setattr(pf, field.name, getattr(profile, field.name))
		pf.check_initialized()
		return pf

	@endpoints.method(ProfileMsg, StringMsg,
			path='', http_method='POST', name='updateProfile')
	def updateProfile(self, request):
		"""Update user profile."""
		return self._updateProfile(request)  # transactional


	###################################################################
	# Match Objects
	###################################################################

	@ndb.transactional(xg=True)
	def _createMatch(self, request):
		"""Create new Match, update user Profile to add new Match to Profile.
		Also notify all applicable users this new match is available to them.
		Returns MatchMsg/request."""
		status = BooleanMsg()
		status.data = False

		token = request.accessToken
		user_id = self._getUserId(token)

		# If any field in request is None, then raise exception
		if any([getattr(request, field.name) is None for field in request.all_fields()]):
			raise endpoints.BadRequestException('All input fields required to create a match')

		# Copy MatchMsg/ProtoRPC Message into dict
		data = {field.name: getattr(request, field.name) for field in request.all_fields()}
		del data['accessToken']  # don't need this for match object

		# Get user profile from NDB
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()

		# Normalize NTRP if needed
		ntrp = profile.ntrp
		if profile.gender == 'f':
			ntrp = profile.ntrp - 0.5

		# Add default values for those missing
		data['players']   = [user_id]
		data['confirmed'] = False
		data['ntrp']      = ntrp

		# Convert date/time from string to datetime object
		dt_string = data['date'] + '|' + data['time']
		dt_string2 = 'on ' + data['date'] + ' at ' + data['time']  # used for notifications
		data['dateTime'] = datetime.strptime(dt_string, '%m/%d/%Y|%H:%M')
		del data['date']
		del data['time']

		# Create new match based on data, and put in datastore
		match_key = Match(**data).put().urlsafe()

		# Update user profile, adding the new match to Profile.matches
		profile.matches.append(match_key)
		profile.put()

		status.data = True
		return status, profile, match_key, dt_string2

	def _notifyAvailMatch(self, profile, match_key, dt_string):
		""" Notify all potential partners of newly created match """
		# FIXME: Put this functionality in a task queue

		# Get name and NTRP of currently player
		player_name = profile.firstName + ' ' + profile.lastName
		my_ntrp = profile.ntrp
		if profile.gender == 'f':
			my_ntrp -= 0.5

		# Query the DB to find partners of similar skill
		query = Profile.query(ndb.OR(Profile.ntrp == my_ntrp, Profile.ntrp == my_ntrp + 0.5, Profile.ntrp == my_ntrp - 0.5))

		for partner in query:
			# Current user does not get notified
			if profile.userId == partner.userId:
				continue

			# Notify the potential partner
			match_url = '?match_type=avail&match_id=' + match_key
			email_message = 'You have a new available match with %s %s.' % (player_name, dt_string)
			email_message += '<br>To view the match, <a href="http://www.georgesungtennis.com/%s">click here</a>.' % match_url

			# Try FB and email notifications
			# The functions themselves will test if FB user and/or if they enabled the notification
			self._postFbNotif(partner.userId, urlquote('New available match with ' + player_name + ' ' + dt_string), match_url)
			self._emailAvailMatch(partner, email_message, player_name)


	@endpoints.method(MatchMsg, BooleanMsg, path='',
		http_method='POST', name='createMatch')
	def createMatch(self, request):
		"""Create new Match"""
		#return self._createMatch(request)

		status, profile, match_key, dt_string = self._createMatch(request)
		self._notifyAvailMatch(profile, match_key, dt_string)

		return status


	@ndb.transactional(xg=True)
	def _joinMatch(self, request):
		"""Join an available Match, given Match's key.
		If there is mid-air collision, return false. If successful, return true."""
		token = request.accessToken
		user_id = self._getUserId(token)

		# If any field in request is None, then raise exception
		if request.data is None:
			raise endpoints.BadRequestException('Need match ID from request.data')

		# Get match key, then get the Match entity from db
		match_key = request.data
		match = ndb.Key(urlsafe=match_key).get()

		# Make sure match is not full. If full, return false.
		match_full = False
		if match.singles and len(match.players) >= 2:
			match_full = True
		elif not match.singles and len(match.players) >= 4:
			match_full = True

		if match_full:
			status = BooleanMsg()
			status.data = False
			return status

		# Update 'players' and 'confirmed' fields (if needed)
		match.players.append(user_id)

		if match.singles or len(match.players) >= 4:
			match.confirmed = True

		# Update Match db
		match.put()

		# Add current match key to current user's matches list
		profile_key = ndb.Key(Profile, user_id)
		profile = profile_key.get()
		profile.matches.append(match_key)
		profile.put()

		# Notify all other players that current user/player has joined the match
		player_name = profile.firstName + ' ' + profile.lastName
		for other_player in match.players:
			if other_player == user_id:
				continue

			match_url = '?match_type=conf_pend&match_id=' + match_key
			email_message = '%s has <b>joined</b> your match. To view your match, <a href="http://www.georgesungtennis.com/%s">click here</a>.' % (player_name, match_url)

			# Try FB and email notifications
			# The functions themselves will test if FB user and/or if they enabled the notification
			self._postFbNotif(other_player, urlquote(player_name + ' has joined your match'), match_url)
			self._emailMatchUpdate(other_player, email_message, player_name, 'joined')

		# Return true, for success
		status = BooleanMsg()
		status.data = True

		return status

	@endpoints.method(StringMsg, BooleanMsg, path='',
		http_method='POST', name='joinMatch')
	def joinMatch(self, request):
		"""Join an available Match, given Match's key"""
		return self._joinMatch(request)


	@ndb.transactional(xg=True)
	def _cancelMatch(self, request):
		"""Cancel an existing Match, given Match's key.
		If successful, return true."""
		status = BooleanMsg()
		status.data = False

		token = request.accessToken
		user_id = self._getUserId(token)

		# If any field in request is None, then raise exception
		if request.data is None:
			raise endpoints.BadRequestException('Need match ID from request.data')
			return status

		# Get match key, then get the Match entity from db
		match_key = request.data
		match = ndb.Key(urlsafe=match_key).get()

		# Determine if cancelling player is the owner of the match
		owner_leaving = match.players[0] == user_id

		# Update 'players' and 'confirmed' fields
		match.players.remove(user_id)
		match.confirmed = False

		# Remove current match key from current user's matches list
		profile = ndb.Key(Profile, user_id).get()
		profile.matches.remove(match_key)
		profile.put()

		# Notify all other players that current user/player has left the match
		player_name = profile.firstName + ' ' + profile.lastName
		match_url = '?match_type=conf_pend&match_id=' + match_key

		for other_player in match.players:
			# Try FB and email notifications
			# The functions themselves will test if FB user and/or if they enabled the notification
			if owner_leaving:
				# FB
				self._postFbNotif(other_player, urlquote(player_name + ' has cancelled your match'), '')

				# Email
				email_message = '%s has <b>cancelled</b> your match. <a href="http://www.georgesungtennis.com/">Click here</a> to visit the homepage.' % player_name
				self._emailMatchUpdate(other_player, email_message, player_name, 'cancelled')
			else:
				# FB
				self._postFbNotif(other_player, urlquote(player_name + ' has left your match'), match_url)

				# Email
				email_message = '%s has <b>left</b> your match. <a href="http://www.georgesungtennis.com/%s">Click here</a> to view your match.' % (player_name, match_url)
				self._emailMatchUpdate(other_player, email_message, player_name, 'left')

			# If owner left, means the entire match is cancelled. Remove this match from other_player's match list
			if owner_leaving:
				other_player_profile = ndb.Key(Profile, other_player).get()
				other_player_profile.matches.remove(match_key)
				other_player_profile.put()

		# Delete or update Match entity
		if owner_leaving:
			match.key.delete()
		else:
			match.put()

		# Return true, for success
		status.data = True
		return status

	@endpoints.method(StringMsg, BooleanMsg, path='',
		http_method='POST', name='cancelMatch')
	def cancelMatch(self, request):
		"""Cancel an existing Match, given Match's key"""
		return self._cancelMatch(request)


	@endpoints.method(StringArrayMsg, BooleanMsg, path='',
		http_method='POST', name='postMatchMsg')
	def postMatchMsg(self, request):
		"""
		Post message to an existing Match, given Match's key
		Match key is in request.data[0], the message is in request.data[1]
		"""
		status = BooleanMsg()
		status.data = False

		# If empty message, return False
		if request.data[1] == '':
			return status

		# Find user's name
		token = request.accessToken
		user_id = self._getUserId(token)
		profile = ndb.Key(Profile, user_id).get()
		player_name = profile.firstName + ' ' + profile.lastName

		# Get match key, then get the Match entity from db
		match_key = request.data[0]
		match = ndb.Key(urlsafe=match_key).get()

		# Add the new message to match messages
		msg = request.data[1]
		match.msgs.append(player_name + '|' + msg)
		match.put()

		# Notify all other players that current user/player has posted a message
		for other_player in match.players:
			if other_player == user_id:
				continue

			match_url = '?match_type=conf_pend&match_id=' + match_key
			email_message = '%s has posted a message in your match. To view your match, <a href="http://www.georgesungtennis.com/%s">click here</a>.' % (player_name, match_url)
			email_message += '<br><br>Message:<br><i>%s</i>' % msg

			# Try FB and email notifications
			# The functions themselves will test if FB user and/or if they enabled the notification
			self._postFbNotif(other_player, urlquote(player_name + ' has posted a message in your match'), match_url)
			self._emailMatchUpdate(other_player, email_message, player_name, 'posted a message in')

		status.data = True
		return status

	@endpoints.method(StringMsg, StringArrayMsg, path='',
		http_method='POST', name='getMatchMsgs')
	def getMatchMsgs(self, request):
		"""
		Get all match messages, given Match's key
		"""
		msgs = StringArrayMsg()

		# Authenticate user
		token = request.accessToken
		user_id = self._getUserId(token)
		if user_id is None:
			return None

		# From match key, get Match entity, and get match messages
		match_key = request.data
		match = ndb.Key(urlsafe=match_key).get()
		msgs.data = match.msgs

		return msgs


	###################################################################
	# Queries
	###################################################################

	def _appendMatchesMsg(self, match, t_delta, matches_msg):
		# Ignore matches in the past, or matches that will occur in less than t_delta minutes
		# Note we store matches in naive time, but datetime.now() returns UTC time,
		# so we use tzinfo object to convert to local time
		if match.dateTime - timedelta(minutes=t_delta) < datetime.now(Eastern_tzinfo()).replace(tzinfo=None):
			return

		# Convert datetime object into separate date and time strings
		date, time = match.dateTime.strftime('%m/%d/%Y|%H:%M').split('|')

		# Convert match.players into pipe-separated 'firstName lastName' string
		# e.g. ['Bob Smith|John Doe|Alice Wonderland|Foo Bar', 'Blah Blah|Hello World']
		players = ''
		for player_id in match.players:
			player_profile = ndb.Key(Profile, player_id).get()

			first_name  = player_profile.firstName
			last_name   = player_profile.lastName
			ntrp        = player_profile.ntrp
			gender      = player_profile.gender.capitalize()

			players += first_name + ' ' + last_name + ' (' + str(ntrp) + gender + '), '
		players = players.rstrip(', ')

		# Populate fields in matches_msg
		matches_msg.singles.append(match.singles)
		matches_msg.date.append(date)
		matches_msg.time.append(time)
		matches_msg.location.append(match.location)
		matches_msg.players.append(players)
		matches_msg.confirmed.append(match.confirmed)
		matches_msg.key.append(match.key.urlsafe())

		# No need to return anything, matches_msg is a reference, so you modified the original thing


	@endpoints.method(AccessTokenMsg, MatchesMsg,
			path='', http_method='POST', name='getMyMatches')
	def getMyMatches(self, request):
		"""Get all confirmed or pending matches for current user."""
		token = request.accessToken
		user_id = self._getUserId(token)

		# Get user Profile based on userId (email)
		profile = ndb.Key(Profile, user_id).get()

		# Create new MatchesMsg message
		matches_msg = MatchesMsg()

		# For each match is user's matches, add the info to match_msg
		for match_key in profile.matches:
			match = ndb.Key(urlsafe=match_key).get()

			# For confirmed matches, show it up to 1 hour after the match
			# For pending matches, show it up to the exact time of the match
			if match.confirmed:
				t_delta = -60
			else:
				t_delta = 0

			self._appendMatchesMsg(match, t_delta, matches_msg)

		return matches_msg

	@endpoints.method(AccessTokenMsg, MatchesMsg,
			path='', http_method='POST', name='getAvailableMatches')
	def getAvailableMatches(self, request):
		"""
		Get all available matches for current user.
		Search through DB to find partners of similar skill.
		"""
		token = request.accessToken
		user_id = self._getUserId(token)

		# Get user Profile based on userId
		profile = ndb.Key(Profile, user_id).get()

		# Create new MatchesMsg message
		matches_msg = MatchesMsg()

		# Women's NTRP is equivalent to -0.5 men's NTRP, from empirical observation
		my_ntrp = profile.ntrp
		if profile.gender == 'f':
			my_ntrp -= 0.5

		# Query the DB to find matches where partner is of similar skill
		query = Match.query(ndb.OR(Match.ntrp == my_ntrp, Match.ntrp == my_ntrp + 0.5, Match.ntrp == my_ntrp - 0.5))
		query = query.order(Match.dateTime)  # ascending datetime order (i.e. earliest matches first)

		for match in query:
			# Ignore matches current user is already participating in
			if profile.userId in match.players:
				continue

			# Ignore matches that are full
			if (match.singles and len(match.players) == 2) or (not match.singles and len(match.players) == 4):
				continue

			# Only show available matches that occur in less than 1 hour from now
			self._appendMatchesMsg(match, 60, matches_msg)

		return matches_msg


# registers API
api = endpoints.api_server([TennisApi])
