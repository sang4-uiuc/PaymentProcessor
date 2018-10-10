from flask import Flask, jsonify, request, session, abort
import stripe
import os
import datetime
import plaid
import json 
import sys
import pyrebase
import string_utilities
import time
import firebase_admin
from firebase_admin import messaging
from firebase_admin import credentials




config = {
  'apiKey': '',
  'authDomain': '',
  'databaseURL': '',
  'storageBucket': '',
  'serviceAccount': '',
}

stripe.api_key = ''
PLAID_CLIENT_ID = ''
PLAID_SECRET = ''
PLAID_PUBLIC_KEY = ''
cred = credentials.Certificate('')
default_app = firebase_admin.initialize_app(cred)
application = Flask(__name__)

firebase = pyrebase.initialize_app(config)
auth = firebase.auth()
db = firebase.database()

PLAID_ENV = 'development'


client = plaid.Client(client_id = PLAID_CLIENT_ID, secret=PLAID_SECRET,
                      public_key=PLAID_PUBLIC_KEY, environment=PLAID_ENV)

#access_token = None
public_token = None

# route used to create Stripe customers
@application.route('/create_customer', methods=['POST'])
def create_stripe_customer():
  post_response = request.get_json()
  uid = post_response.get('uid')
  try:
    customer = stripe.Customer.create()
    stripe_id = customer.get('id')
    db.child('stripe_information').child(uid).child("stripe_id").set(stripe_id)
  except:
    abort(400, {'message': 'could not create the customer on Stripe'})
  return jsonify({
    'message': 'customer successfully created on Stripe',
    'stripe_id': stripe_id,
    }), 200

# used for all normal push notifications
@application.route('/pay_notification', methods = ['POST'])
def pay_notification():
  post_response = request.get_json()
  title = post_response.get('title')
  payload = post_response.get('message')
  deviceToken = post_response.get('token')
  message = messaging.Message(notification=messaging.Notification(title=title, body=payload), token=deviceToken)
  try:
    response = messaging.send(message)
  except:
    abort(400, {'message': 'could not send push notification'})
  return jsonify({'message': 'notification successfully sent from customer'}), 200

# silent notification
@application.route('/silent_notification', methods = ['POST'])
def silent_notification():
  post_response = request.get_json()
  deviceToken = post_response.get('token')
  key = post_response.get('key')
  message = messaging.Message(data = {"key" : key},
                              token = deviceToken,
                              apns = messaging.APNSConfig(payload = messaging.APNSPayload(
                                                         aps = messaging.Aps(content_available = True))))
  
  try:
    response = messaging.send(message)
  except:
    abort(400, {'message': 'could not send silent notification'})
  return jsonify({'message': 'silent notification successfully sent from customer'}), 200


# route used to create ephermeral key for Stripe UI usage
@application.route('/create_ephemeral_key', methods=['POST'])
def issue_key():
  post_response = request.get_json()
  stripe_id, api_version = post_response.get('stripe_id'), post_response.get('api_version')
  try:
    key = stripe.EphemeralKey.create(customer=stripe_id, api_version=api_version)
  except:
    abort(400, {'message': 'could not create ephemeral key for Stripe customer'})
  return jsonify(key), 200

# route used to add a card to Stripe customer
@application.route('/add_card', methods=['POST'])
def add_card_to_user():
  post_response = request.get_json()
  stripe_id, token_id = post_response.get('stripe_id'), post_response.get('token_id')
  try:
    customer = stripe.Customer.retrieve(stripe_id)
    customer.sources.create(source=token_id)
  except stripe.error.CardError as e:
    body = e.json_body
    err  = body.get('error', {})
    print("Status is: %s" % e.http_status)
    print("Type is: %s" % err.get('type'))
    print("Code is: %s" % err.get('code'))
    # param is '' in this case
    print("Param is: %s" % err.get('param'))
    print("Message is: %s" % err.get('message'))

    abort(400, {'message': 'could not attach card to Stripe customer'})
  return jsonify({'message': 'card successfully added to customer'}), 200

# webhook for source created events
@application.route('/source_created', methods=['POST'])
def update_source_status_in_firebase(): 
  post_response = request.get_json()
  source_id = post_response.get('data').get('object').get('id')
  stripe_id = post_response.get('data').get('object').get('customer')
  try:
#    user = auth.sign_in_with_email_and_password(PYREBASE_USERNAME, PYREBASE_PASSWORD)
    uid_query = db.child('stripe_information').order_by_child('stripe_id').equal_to(stripe_id).get()
    uid = next(iter(uid_query.val().items()))[0]
    db.child('sources').child(uid).child('sources_list').child(source_id).update({
      'status': 'APPROVED'
      })
  except:
    abort(400, {'message': 'could not update status for card (add)'})
  return '', 200

# route used to delete asource from a Stripe customer
@application.route('/delete_source', methods=['POST'])
def delete_source_from_stripe():
  post_response = request.get_json()
  stripe_id, source_id = post_response.get('stripe_id'), post_response.get('source_id')
  try:
    customer = stripe.Customer.retrieve(stripe_id)
    customer.sources.retrieve(source_id).delete()
  except stripe.error.CardError as e:
    body = e.json_body
    err  = body.get('error', {})
    print("Status is: %s" % e.http_status)
    print("Type is: %s" % err.get('type'))
    print("Code is: %s" % err.get('code'))
    # param is '' in this case
    print("Param is: %s" % err.get('param'))
    print("Message is: %s" % err.get('message'))
    abort(400, {'message': 'could not delete source from Stripe customer'})
  return jsonify({'message': 'source successfully deleted from customer'}), 200

# webhook for source deleted events
@application.route('/source_deleted', methods=['POST'])
def delete_source_from_firebase():
  post_response = request.get_json()
  source_id = post_response.get('data').get('object').get('id')
  stripe_id = post_response.get('data').get('object').get('customer')
  try:
#    user = auth.sign_in_with_email_and_password(PYREBASE_USERNAME, PYREBASE_PASSWORD)
    uid_query = db.child('stripe_information').order_by_child('stripe_id').equal_to(stripe_id).get()
    uid = next(iter(uid_query.val().items()))[0]
    db.child('sources').child(uid).child('sources_list').child(source_id).remove()
    new_source_id_query = db.child('sources').child(uid).child('sources_list').get()
    if new_source_id_query.val() is None:
      db.child('sources').child(uid).remove()
    else:
      remaining_sources = new_source_id_query.val().items()
      new_source = next(iter(remaining_sources))[0]
      db.child('sources').child(uid).child('sources_list').child(new_source).update({'is_default': True,})
      db.child('sources').child(uid).update({'default_source': new_source})

  except:
    abort(400, {'message': 'could not update status for card (delete)'})
  return '', 200

@application.route('/charge_source', methods=['POST'])
def charge_source():
  post_response = request.get_json()
  source_id = post_response.get('source_id')
  stripe_id = post_response.get('stripe_id')
  amount = post_response.get('amount')
  source_type = post_response.get('source_type')
  try:
    if source_type == "CARD":
      transaction_charge = int(amount * 1.04)
      stripe.Charge.create(amount=transaction_charge, currency='usd', customer=stripe_id, source=source_id)
    elif source_type == "BANK":
      transaction_charge = int(amount)
      stripe.Charge.create(amount=transaction_charge, currency='usd', customer=stripe_id, source=source_id)
  except:
    abort(400, {'message': 'could not charge source'})
  return jsonify({'message': 'customer source successfully charged'}), 200

@application.route('/add_bank', methods=['POST'])
def connect_plaid():
  post_response = request.get_json()
  stripe_id = post_response.get('stripe_id')
  prev_default = post_response.get('prev_default')
  uid = post_response.get('uid')
  global access_token
  # retrieve public token from plaid
  public_token = post_response.get('public_token')
  account_id = post_response['metadata']['account_id']
  # retrieve access token from plaid
  try:
    exchange_response = client.Item.public_token.exchange(public_token)
    access_token = exchange_response['access_token']
#    user = auth.sign_in_with_email_and_password(PYREBASE_USERNAME, PYREBASE_PASSWORD)
    # create stripe token to connect with stripe
    stripe_response = client.Processor.stripeBankAccountTokenCreate(access_token, account_id)
    bank_account_token = stripe_response['stripe_bank_account_token']
    token = stripe.Token.retrieve(bank_account_token)
    bank_id = token['bank_account']['id']
    last4 = token['bank_account']['last4']
    customer = stripe.Customer.retrieve(stripe_id)
    customer.sources.create(source=bank_account_token)
    if prev_default is not None :
      db.child('sources').child(uid).child('sources_list').child(prev_default).update({
        'is_default': False,
        })
    db.child('sources').child(uid).update({
      'default_source': bank_id
      })
    db.child('sources').child(uid).child('sources_list').child(bank_id).set({
      'is_default': True,
      'last4': last4,
      'status': 'PENDING',
      'type': 'BANK',
      'brand': 0,
      })
  except:
    abort(400, {'message': 'could not attach bank to Stripe customer'})
  return jsonify({'message': 'bank successfully added to customer'}), 200

if __name__ == '__main__':
  application.run(host = '0.0.0.0', debug=True)
