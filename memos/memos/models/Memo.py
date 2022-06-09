"""
The model file for a Memo

"""
import re
import os
import shutil
import json
from datetime import datetime

from flask import current_app, url_for
from flask_mail import Message

from memos import db, mail
from memos.models.User import User
from memos.models.MemoState import MemoState
from memos.models.MemoFile import MemoFile
from memos.models.MemoSignature import MemoSignature
from memos.models.MemoReference import MemoReference
from memos.models.MemoHistory import MemoHistory
from memos.models.MemoActivity import MemoActivity
from memos.revletter import b10_to_rev, rev_to_b10

class Memo(db.Model):
    """This class is the single interface to a "memo" and all of the "memos"
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(120), db.ForeignKey('user.username'),
        nullable=False)                                                 # The key of the user who owns the memo
    number = db.Column(db.Integer)                                      # Memo Number
    version = db.Column(db.String(2))                                   # A,B,..Z,AA,AB,...AZ,BA
    confidential = db.Column(db.Boolean, default=False)                 # if true only author, signer, distribution can read
    pinned = db.Column(db.Boolean, default=False)                       # Can be pinned to top of memo list
    template = db.Column(db.Boolean, default=False)                     # The memo is used as a template for the system
    distribution = db.Column(db.String(128), default='')                # user names on the distribution
    keywords = db.Column(db.String(128), default='')                    # any keyword
    title = db.Column(db.String(128), nullable=False, default='')       # The title of the memo

    action_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # The last time anything happened
    create_date = db.Column(db.DateTime)    # when the memo was created
    submit_date = db.Column(db.DateTime)    # when the memo was most recently submitted  (from created)
    active_date = db.Column(db.DateTime)    # when the memo was moved to active state (from submitted)
    obsolete_date = db.Column(db.DateTime)  # when the memo was moved to obsolete state (from active)
    
    _signers = db.Column(db.String(128),default='')                                 # the hidden list of signer usernames
    _references = db.Column(db.String(128),default='')                              # The hidden list of references
    memo_state = db.Column(db.Enum(MemoState))                                      # Draft, Signoff, Active, Obsolete

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # do custom initialization here

    def __repr__(self):
        return f"{self.user.username}-{self.number}{self.version}"

    def __str__(self):
        return f"{self.user.username}-{self.number}{self.version}"

########################################
# Permission Functions
########################################

    @staticmethod
    def can_create(owner=None, delegate=None):
        """Will return true if the delegate can create a memo for the owner"""

        if owner is None:
            return False

        if delegate is None:
            delegate = owner
       
        return owner.is_delegate(delegate=delegate)

    def can_revise(self, delegate=None):
        """Is the delgate allowed to update "this" memo?"""
        
        if delegate is None:
            return False
        
        if not self.user.is_delegate(delegate):
            return False

        if self.memo_state == MemoState.Active or self.memo_state == MemoState.Obsolete:
            return True

        return False

    def can_sign(self, signer, delegate):
        """Can this memo be signed by delegate for the signers"""
        
        if signer is None or delegate is None:
            return False

        if self.memo_state != MemoState.Signoff:
            return False

        if not signer.is_delegate(delegate=delegate):
            return False

        # The list of signers and if they have signed are kept in the MemoSignature table
        status = MemoSignature.is_signer(self.id,signer)
        return status['is_signer'] and not status['status']

    def can_unsign(self, signer, delegate):
        """Can this memo be unsigned by delegate for the signer """
        if signer is None or delegate is None:
            return False

        if self.memo_state != MemoState.Signoff:
            return False

        if not signer.is_delegate(delegate=delegate):
            return False

        status = MemoSignature.is_signer(self.id,signer)
        return status['is_signer'] and status['status']

    def can_obsolete(self, delegate):
        """ Can this memo be obsoleted by the delegate?  Only active memos can be obsoleted """
        if delegate is None:
            return False

        if not self.user.is_delegate(delegate):
            return False

        if self.memo_state == MemoState.Active:
            return True

        return False

    def can_cancel(self, delegate):
        """ can this memo be cancled by the delegate.  Only drafts memos can be canceled"""
        if delegate is None:
            return False

        if self.memo_state != MemoState.Draft:
            return False

        if not self.user.is_delegate(delegate=delegate):
            return False

        return True

    def can_reject(self, signer, delegate):
        """ can this memo be rejected by the delegate.  Only memos in signoff can be rejected"""
        if signer is None or delegate is None:
            return False

        if self.memo_state != MemoState.Signoff:
            return False

        if not signer.is_delegate(delegate):
            return False

        status = MemoSignature.is_signer(memo_id=self.id,signer=signer)

        # if you are a signer you can reject.. even if you have already signed
        return status['is_signer']

    def has_access(self, user=None):
        """This function will return True of the "username" has access to self"""

        # if it is not confidential than anyone can access
        if self.confidential == False:
            return True

        # at this point we know it is confidential so ... they must provide a username
        if user is None:
            return False

        # you alway have access to your own memo's
        if self.user.username == user.username:
            return True

        if user.admin:
            return True
        
        if user.readAll:
            return True

        # if the username is in the distribution list then provide access TODO: ARH do something better
        if user.username in re.split('\s|\,|\t|\;|\:',self.distribution):
            return True

        return False


########################################
# ??? Functions
########################################

    def get_fullpath(self):
        """ This function gives the os path to a file """    
        path = os.path.join(current_app.root_path,"static","memos",f"{self.user_id}",f"{self.number}",f"{self.version}")
        return path

    @property
    def files(self):
        """ Return a list of the files attached to this memo"""
        memo_files = MemoFile.query.filter_by(memo_id=self.id).all()
        return memo_files

    def saveJson(self):
        """ Create the JSON file which is a copy of all of the meta data """
        js = {}
        js['userid']=self.user_id
        js['number']=self.number
        js['version']=self.version
        js['title']=self.title
        if self.active_date:
            js['active_date']=self.active_date.strftime("%m/%d/%Y")
        if self.obsolete_date:
            js['obsolete_date']=self.obsolete_date.strftime("%m/%d/%Y")
        js['confidential']=self.confidential
        js['distribution']=self.distribution
        js['keywords']=self.keywords
        js['memo_state']=f"{self.memo_state}"
        js['keywords']= self.keywords
        # need to write the date of the signer
        user = User.find(username=self.user_id)
        signlist = MemoSignature.get_signers(self)
        signers = []
        for sig in signlist:
            if sig.date_signed is not None:
                signers.append((sig.signer_id,f"{datetime.strftime(sig.date_signed,'%m/%d/%Y')}"))
            
        js['signers']=signers
        js['references']= self.references['ref_string']
        js['files']=[]
        for file in self.files:
            js['files'].append((file.filename,file.uuid))

        path = os.path.join(self.get_fullpath())
        #current_app.logger.info(f"Making Directory {path}")
        os.makedirs(path,exist_ok=True)
        #current_app.logger.info(f"Making Succeeded {path}")

        path = os.path.join(path,f"meta-{self.user_id}-{self.number}-{self.version}.json")
        f = open(path,"w")
        json.dump(js,f)
        f.close()

    @property
    def signers(self):
        # get the signers from the signing table and turn it back to a string and a list
        siglist = MemoSignature.get_signers(self)
        for sig in siglist:
            sig.signer = User.find(username=sig.signer_id)
            sig.delegate = User.find(username=sig.delegate_id)
        return {'signers':self._signers,'siglist':siglist}

    @signers.setter
    def signers(self,signer_names):
        self._signers = signer_names
        MemoSignature.delete_signers(self)

        users = User.valid_usernames(signer_names)

        for signer in users['valid_users']:
            MemoSignature.add_signer(memo=self,signer=signer)

######################################################################
# References
######################################################################

    @staticmethod
    def parse_reference(reference):
        parts = re.split(r'-',reference)
        if len(parts) == 2:
            parts.append(None)
        return parts
            
    @staticmethod
    def valid_references(references):
        current_app.logger.info(f'references ={references}')
        valid_memos = []
        valid_refs = []
        invalid = []
        for memo_ref in re.split(r'\s|\,|\t|\;|\:',references):
            if memo_ref == '':
                continue
            parts = Memo.parse_reference(memo_ref)
            if len(parts) > 3 or len(parts) < 2:
                invalid.append(memo_ref)
                current_app.logger.info(f"INVALID length append {memo_ref} valid={valid_memos} invalid {invalid}")
                continue

            username = parts[0]
            memo_number = parts[1]
            memo_version = parts[2]
            memo = Memo.find(username=username,memo_number=memo_number,memo_version=memo_version)
            current_app.logger.info(f"Memo = {memo}")
            if memo != None and (memo.memo_state == MemoState.Active or memo.memo_state == MemoState.Obsolete):
                valid_memos.append(memo)
                valid_refs.append(memo_ref)
            else:
                invalid.append(memo_ref)
        
        rval = {'valid_refs':valid_refs, 'valid_memos' : valid_memos,'invalid':invalid}
        return rval
            
    @property
    def references(self):
        # this function will return a list of reference objects + a string of the references
        refs = []
        ref_list = MemoReference.query.filter_by(source_id=self.id).all()
        for ref in ref_list:
            if ref.ref_memo_version == None:
                refstring=f"{ref.ref_user_id}-{ref.ref_memo_number}"
            else:
                refstring=f"{ref.ref_user_id}-{ref.ref_memo_number}-{ref.ref_memo_version}"
            refs.append(refstring)
        return {'reflist':refs,'ref_string':r' '.join(refs)}
    
    @references.setter
    def references(self,references):
        self._references = references

        MemoReference.query.filter_by(source_id=self.id).delete()
        refs = Memo.valid_references(references)
        for i in range(len(refs['valid_refs'])):
            parsed_ref = Memo.parse_reference(refs['valid_refs'][i])
            user = User.find(username=parsed_ref[0])
            MemoReference.add_ref(self.id,ref_user_id=user.username,ref_memo_number=parsed_ref[1],ref_memo_version=parsed_ref[2])

    @property
    def backrefs(self):
        # this function will return a list of reference objects + a string of the references
        refs=[]
        ref_list = MemoReference.query.filter_by(ref_user_id=self.user_id,ref_memo_number=self.number).all()
        for ref in ref_list:
            if ref.ref_memo_version and ref.ref_memo_version != self.version:
                continue

            memo = Memo.query.get(ref.source_id)
            if not memo:
                continue # pragma nocover  - This only happens if a memo was deleted without cascading to the Memoreference.
            refstring=f"{memo.user_id}-{memo.number}-{memo.version}"

            refs.append(refstring)
            refs = list(set(refs))
        return {'reflist':refs,'ref_string':' '.join(refs)}
        
######################################################################
# 
######################################################################

    def get_next_version(self):
        return b10_to_rev(rev_to_b10(self.version)+1)

    def save(self):
        db.session.add(self)
        self.saveJson()


################################################################################       
# functions used to process the state   
# these function would classiavally be called private
################################################################################       

    def obsolete_previous(self,acting=None):
        prev_list = Memo.query.join(User).filter(Memo.number == self.number,Memo.version != self.version).all()
        for memo in prev_list:
            if memo.memo_state == MemoState.Active:
                memo.memo_state = MemoState.Obsolete
                MemoHistory.activity(memo=memo,memo_activity=MemoActivity.Obsolete,user=acting)
                memo.save()

    # This function is called when:
    # 1- a valid draft is created
    # 2- a signature happens
    # 3- an unsign happens
    def process_state(self,acting=None):
        self.action_date = datetime.utcnow()
        if self.memo_state == MemoState.Draft:
            if MemoSignature.status(self.id) == False:
                self.memo_state = MemoState.Signoff
                self.submit_date = self.action_date
                MemoHistory.activity(memo=self,memo_activity=MemoActivity.Signoff,user=acting)
                self.save()
                self.notify_signers(f"memo {self.user.username}-{self.number}-{self.version} has gone into signoff")
            else:
                self.memo_state = MemoState.Active
                self.active_date = self.action_date
                MemoHistory.activity(memo=self,memo_activity=MemoActivity.Activate,user=acting)
                self.obsolete_previous(acting=acting)
                self.save()
                self.notify_distribution(f"memo {self.user.username}-{self.number}-{self.version} has been published")
   
        if self.memo_state == MemoState.Signoff:
            if MemoSignature.status(self.id):
                self.memo_state = MemoState.Active
                self.active_date = self.action_date
                MemoHistory.activity(memo=self,memo_activity=MemoActivity.Activate,user=acting)
                self.obsolete_previous(acting=acting)
                self.save()
                self.notify_distribution(f"memo {self.user.username}-{self.number}-{self.version} has been published")
            else:
                current_app.logger.info(f"Signatures Still Required")
        



    def notify_distribution(self,message):
        try:
            replyTo = User.find(self.user_id)
            users = User.valid_usernames(self.distribution)
            recipients=[]
            for email in users['email_addrs']:
                recipients.append(email)
                
            # Only send emails if there is a distribution list.
            if len(recipients) > 0:
                msg = Message(message,
                            sender=os.environ['MEMOS_EMAIL_USER'],
                            recipients=recipients,
                            reply_to=replyTo.email)
                msg.body = f'''{message}
            Use the following link:
            {url_for('memos.main', username=self.user_id, memo_number=self.number, memo_version=self.version, _external=True)}?detail
            '''
                if 'MEMOS_EMAIL_SERVER' in os.environ:
                    mail.send(msg)
                else: # pragma nocover
                    current_app.logger.info(F"Notify Distribution {self.distribution} {message}")
                
        except BaseException as e: # pragma nocover
            raise e

    def notify_signers(self,message):
        current_app.logger.info(F"Notify signers {message}")
        try:            
            replyTo = User.find(self.user_id)
            signlist = MemoSignature.get_signers(self)
            recipients=[]
            for recipient in signlist:
                signer = User.find(recipient.signer_id)
                if len(signer.email) > 2:
                    recipients.append(signer.email)
            msg = Message(message,
                        sender=os.environ['MEMOS_EMAIL_USER'],
                        recipients=recipients,
                        reply_to=replyTo.email)
            msg.body = f'''{message}
        Use the following link:
        {url_for('memos.main', username=self.user_id, memo_number=self.number, memo_version=self.version, _external=True)}?detail
        '''
            if 'MEMOS_EMAIL_SERVER' in os.environ:
                mail.send(msg)
            else: # pragma nocover
                current_app.logger.info(F"Notify Signers {self.distribution} {message}")


        except BaseException as e: # pragma nocover
            raise e
################################################################################
# State machine functions called by the viewcontroller
################################################################################

# Owner Function
    @staticmethod
    def create_revise(owner,delegate,memo_number=None):
        """ This function will return None or a new Memo if the owner/delgate and revise this memo """ 
        if owner.is_delegate(delegate) != True:
            return None

        memo = Memo.query.join(User).filter(User.username==owner.username,Memo.number==memo_number).order_by(Memo.version.desc()).first()
 
        # create a new memo (i.e. not a new version of an existing memo)
        if memo_number == None or memo==None:
            memo_number = Memo.get_next_number(owner)
        
            new_memo = Memo(number = memo_number,\
                            version = 'A',\
                            confidential = False,\
                            distribution = '',\
                            keywords = '',\
                            title = '',\
                            user_id = owner.username,\
                            memo_state = MemoState.Draft,\
                            action_date = datetime.utcnow(),\
                            create_date = datetime.utcnow(),\
                            signers = '' )
            
            new_memo.save()
            MemoHistory.activity(memo=new_memo,memo_activity=MemoActivity.Create,user=delegate)
            
            current_app.logger.info(f"Creating new memo {new_memo}")
            return new_memo
       
        
        if memo.memo_state == MemoState.Draft:
            current_app.logger.info(f"Found a draft memo {memo}")
            return memo
 
        # revise an existing memo
        new_memo = Memo(number = memo_number,\
                            version = memo.get_next_version(),\
                            confidential = memo.confidential,\
                            distribution = memo.distribution,\
                            keywords = memo.keywords,\
                            title = memo.title,\
                            user_id = memo.user_id,\
                            memo_state = MemoState.Draft,\
                            action_date = datetime.utcnow(),\
                            create_date = datetime.utcnow(),\
                             )
        new_memo.save()
        new_memo.references = memo.references['ref_string']  # cannot be done until there is an id assigned by the save
        new_memo.signers = memo._signers                     # cannot be done until there is an id assigned by the save
        new_memo.save()
        MemoHistory.activity(memo=new_memo,memo_activity=MemoActivity.Create,user=delegate)
        return new_memo

# signer function
    def sign(self,signer=None,delegate=None):

        current_app.logger.debug(f"signer = {signer} delegate={delegate}")
        if not self.can_sign(signer,delegate):
            current_app.logger.info(f"signer = {signer} delegate={delegate} NOT!!@ allowed to sign")
            return False
        
        current_app.logger.info("allowed to sign")
        MemoSignature.sign(self.id,signer,delegate)
        MemoHistory.activity(memo=self,user=delegate,memo_activity=MemoActivity.Sign)
        self.process_state(acting=delegate)
        return True

# signer function     
    def unsign(self,signer=None,delegate=None):
        
        if not self.can_unsign(signer,delegate):
            return False
        
        MemoSignature.unsign(self.id,signer)
        MemoHistory.activity(memo=self,user=delegate,memo_activity=MemoActivity.Unsign)
        self.process_state(acting=delegate)
        return True
       
# Owner Function       
    def obsolete(self,delegate=None):
        
        current_app.logger.info(f"Obsolete: {self} Delegate={delegate}")
        
        if not self.can_obsolete(delegate=delegate):
            return False
        
        self.memo_state = MemoState.Obsolete
        self.action_date = datetime.utcnow()
        self.obsolete_date = datetime.utcnow()
        MemoHistory.activity(memo=self,user=delegate,memo_activity=MemoActivity.Obsolete)
        self.save()
        return True

# Owner Function
    def cancel(self,delegate):
        current_app.logger.info(f"Cancel: {self} Delegate={delegate}")
    
        memostring = f"{self}"
        
        if not self.can_cancel(delegate=delegate):
            return False
        
        
        MemoFile.delete(self)
        # delete all of the files in that directory & the directory
        
        shutil.rmtree(self.get_fullpath(), ignore_errors=True)
        
        MemoReference.delete(self)
        MemoSignature.delete_signers(self)
        MemoHistory.activity(memo=self,user=delegate,memo_activity=MemoActivity.Cancel)

        db.session.delete(self)
        current_app.logger.info(f"Canceling")
        
        return True

# signer function
    def reject(self,signer,delegate):

        current_app.logger.info(f"signer = {signer} delegate={delegate}")
        if not self.can_reject(signer,delegate):
            return False
        
        
        self.memo_state = MemoState.Draft
        self.action_date = datetime.utcnow()
        self.submit_date = None
        self.active_date = None
        self.obsolete_date = None
        MemoHistory.activity(memo=self,memo_activity=MemoActivity.Reject,user=delegate)
        MemoSignature.unsign_all(self)
        self.save()
        self.notify_signers(f"Memo {self.user.username}-{self.number}-{self.version} has been rejected for {signer.username} by {delegate.username}")
        return True
    

################################################################################       
# End of State machine functions
################################################################################       

    @staticmethod
    def find(memo_id=None,username=None,memo_number=None,memo_version=None):

        current_app.logger.debug(f"FIND: Looking for {username}/{memo_number}/{memo_version}")

        memoQry = Memo.query.filter_by(user_id=username,number=memo_number)
        if memo_version != None:
            memoQry = memoQry.filter_by(version=memo_version)
        memo = memoQry.first()        
        
        current_app.logger.debug(f"Found Memo id={memo}")                                
        return memo

    @staticmethod
    def get_memo_list(username=None,memo_number=None,memo_version=None,page=1,pagesize=None):

        if memo_version:
            memo_list = Memo.query.join(User).filter(User.username==username,\
                                                Memo.number==memo_number,\
                                                Memo.version==memo_version)\
                                                    .order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)
        elif memo_number:
            memo_list = Memo.query.join(User).filter(User.username==username,Memo.number==memo_number)\
            .order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)

        elif username:
            memo_list = Memo.query.join(User).filter(User.username==username,Memo.memo_state == MemoState.Active)\
            .order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)
        else:
            memo_list = Memo.query.join(User).filter(Memo.memo_state == MemoState.Active)\
            .order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)
    
        return memo_list
    
    @staticmethod 
    def search(title=None,keywords=None,page=1,pagesize=None):
        memo_list = None
        current_app.logger.info(f"Search title={title}")
        if title != None:
            memo_list = Memo.query.filter(Memo.title.like(f"%{title}%")).order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)
        
        if keywords != None:
            memo_list = Memo.query.filter(Memo.keywords.like(f"%{keywords}%")).order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)
            
        return memo_list

    @staticmethod   
    def get_next_number(user=None):
        assert user!=None
                
        memo_list = Memo.query.join(User).filter(User.username==user.username)\
            .order_by(Memo.number.desc()).first()
        
        if memo_list == None:
            return 1
        return memo_list.number+1
        

    @staticmethod
    def get_inbox(user,page=1,pagesize=None):
        if user == None:
            return None
        
        msigs = MemoSignature.get_signatures(user,signed=False)
        
        memolist = Memo.query.join(User).filter(Memo.memo_state==MemoState.Signoff,Memo.id.in_(msigs)).order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)      
        current_app.logger.info(f"Inbox for {user.username} = Items={len(memolist.items)} {memolist}")
        return memolist
    
    @staticmethod
    def get_drafts(user,page=1,pagesize=None):
        if user == None:
            return None
        
        memolist = Memo.query.join(User).filter(Memo.memo_state==MemoState.Draft,User.username==user.username).order_by(Memo.action_date.desc()).paginate(page = page,per_page=pagesize)      
        return memolist