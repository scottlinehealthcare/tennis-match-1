'''
Models

If you need to update the model schema live, see:
https://cloud.google.com/appengine/articles/update_schema
'''

import endpoints
from protorpc import messages
from google.appengine.ext import ndb

import datetime

##############################################
# User profile, and its messages
##############################################
class Profile(ndb.Model):
	userId        = ndb.StringProperty(required=True)
	contactEmail  = ndb.StringProperty(default='')
	firstName     = ndb.StringProperty(default='')
	lastName      = ndb.StringProperty(default='')
	gender        = ndb.StringProperty(default='')  # m/f
	ntrp          = ndb.FloatProperty(default=0.0)
	matches       = ndb.StringProperty(repeated=True)  # match keys (store urlsafe version), dynamically changing
	loggedIn      = ndb.BooleanProperty(default=False)
	salt_passkey  = ndb.StringProperty(default='')
	session_id    = ndb.StringProperty(default='')
	emailVerified = ndb.BooleanProperty(default=False)
	notifications = ndb.BooleanProperty(repeated=True)  # [fb_notif_en, email_notif_en]
	pristine      = ndb.BooleanProperty(default=True)  # once user first updates Profile, it's not pristine anymore

class ProfileMsg(messages.Message):
	userId        = messages.StringField(1)
	contactEmail  = messages.StringField(2)
	firstName     = messages.StringField(3)
	lastName      = messages.StringField(4)
	gender        = messages.StringField(5)  # m/f
	ntrp          = messages.FloatField(6)
	accessToken   = messages.StringField(7)
	loggedIn      = messages.BooleanField(8)
	emailVerified = messages.BooleanField(9)
	notifications = messages.BooleanField(10, repeated=True)

class AccountAuthMsg(messages.Message):
	email     = messages.StringField(1)
	password  = messages.StringField(2)
	recaptcha = messages.StringField(3)

class ChangePasswordMsg(messages.Message):
	oldPw       = messages.StringField(1)
	newPw       = messages.StringField(2)
	accessToken = messages.StringField(3)


##############################################
# Match object, and its messages
##############################################
class Match(ndb.Model):
	singles   = ndb.BooleanProperty(required=True)
	dateTime  = ndb.DateTimeProperty(required=True)  # naive time, no time zone info, assuming "local" timezone
	location  = ndb.StringProperty(required=True)  # no need for GeoPtProperty
	players   = ndb.StringProperty(repeated=True)  # userIds
	confirmed = ndb.BooleanProperty(required=True)
	ntrp      = ndb.FloatProperty(required=True)  # NTRP rating of owner of match, standardized to male rating
	msgs      = ndb.StringProperty(repeated=True)  # messages posted by players in a match

class MatchMsg(messages.Message):
	singles   = messages.BooleanField(1)
	date      = messages.StringField(2)
	time      = messages.StringField(3)
	location  = messages.StringField(4)
	players   = messages.StringField(5, repeated=True)  # user 'firstName lastName' (*not* userId)
	confirmed = messages.BooleanField(6)
	ntrp      = messages.FloatField(7)
	accessToken = messages.StringField(8)

# Represents multiple matches
# Each entry in 'players' field is pipe-separated name string, e.g.
# players = ['Bob Smith|John Doe|Alice Wonderland|Foo Bar', 'Blah Blah|Hello World', 'George Sung']
class MatchesMsg(messages.Message):
	singles    = messages.BooleanField(1, repeated=True)
	date       = messages.StringField(2, repeated=True)
	time       = messages.StringField(3, repeated=True)
	location   = messages.StringField(4, repeated=True)
	players    = messages.StringField(5, repeated=True)
	confirmed  = messages.BooleanField(6, repeated=True)
	key        = messages.StringField(7, repeated=True)  # ndb key for each Match entity
	accessToken = messages.StringField(8)


##############################################
# Access token message
##############################################
class AccessTokenMsg(messages.Message):
	accessToken = messages.StringField(1)


##############################################
# Generic data messages
##############################################
class BooleanMsg(messages.Message):
	data = messages.BooleanField(1)
	accessToken = messages.StringField(2)

class StringMsg(messages.Message):
	data = messages.StringField(1)
	accessToken = messages.StringField(2)

class StringArrayMsg(messages.Message):
	data = messages.StringField(1, repeated=True)
	accessToken = messages.StringField(2)
