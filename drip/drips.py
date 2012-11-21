from django.conf import settings
from django.contrib.auth.models import User
from django.template import Context, Template
from django.core.mail import EmailMultiAlternatives
from django.utils.importlib import import_module
from django.utils.html import strip_tags

try:
    from django.utils.timezone import now as conditional_now
except ImportError:
    from datetime import datetime
    conditional_now = datetime.now

from drip.models import SentDrip


def get_email_instance(*args, **kwargs):
    path = getattr(settings, 'DRIP_EMAIL_CLASS', 'drip.drips.DripEmail')
    mod_name, klass_name = path.rsplit('.', 1)
    mod = import_module(mod_name)
    klass = getattr(mod, klass_name)
    return klass(*args, **kwargs)


class DripEmail(object):

    def __init__(self, drip_base, user):
        self.drip_base = drip_base
        self.user = user
        self._context = None
        self._subject = None
        self._body = None
        self._plain = None
        self._email = None

    @property
    def from_email(self):
        return self.drip_base.from_email

    @property
    def from_email_name(self):
        return self.drip_base.from_email_name

    @property
    def context(self):
        if not self._context:
            self._context = Context({'user': self.user})
        return self._context

    @property
    def subject(self):
        if not self._subject:
            self._subject = Template(self.drip_base.subject_template).render(self.context)
        return self._subject

    @property
    def body(self):
        if not self._body:
            self._body = Template(self.drip_base.body_template).render(self.context)
        return self._body

    @property
    def plain(self):
        if not self._plain:
            self._plain = strip_tags(self.body)
        return self._plain

    @property
    def email(self):
        if not self._email:
            if self.drip_base.from_email_name:
                from_ = "%s <%s>" % (self.drip_base.from_email_name, self.drip_base.from_email)
            else:
                from_ = self.drip_base.from_email

            self._email = EmailMultiAlternatives(
                self.subject, self.plain, from_, [self.user.email])

            # check if there are html tags in the rendered template
            if len(self.plain) != len(self.body):
                self._email.attach_alternative(self.body, 'text/html')
        return self._email


class DripBase(object):
    """
    A base object for defining a Drip.

    You can extend this manually, or you can create full querysets
    and templates from the admin.
    """
    #: needs a unique name
    name = None
    subject_template = None
    body_template = None
    from_email = None
    from_email_name = None

    def __init__(self, drip_model, *args, **kwargs):
        self.drip_model = drip_model

        self.name = kwargs.pop('name', self.name)
        self.from_email = kwargs.pop('from_email', self.from_email)
        self.from_email_name = kwargs.pop('from_email_name', self.from_email_name)
        self.subject_template = kwargs.pop('subject_template', self.subject_template)
        self.body_template = kwargs.pop('body_template', self.body_template)

        if not self.name:
            raise AttributeError('You must define a name.')

        self.now_shift_kwargs = kwargs.get('now_shift_kwargs', {})


    #########################
    ### DATE MANIPULATION ###
    #########################

    def now(self):
        """
        This allows us to override what we consider "now", making it easy
        to build timelines of who gets what when.
        """
        return conditional_now() + self.timedelta(**self.now_shift_kwargs)

    def timedelta(self, *a, **kw):
        """
        If needed, this allows us the ability to manipuate the slicing of time.
        """
        from datetime import timedelta
        return timedelta(*a, **kw)

    def walk(self, into_past=0, into_future=0):
        """
        Walk over a date range and create new instances of self with new ranges.
        """
        walked_range = []
        for shift in range(-into_past, into_future):
            kwargs = dict(drip_model=self.drip_model,
                          name=self.name,
                          now_shift_kwargs={'days': shift})
            walked_range.append(self.__class__(**kwargs))
        return walked_range

    def apply_queryset_rules(self, qs):
        for queryset_rule in self.drip_model.queryset_rules.all():
            qs = queryset_rule.apply(qs, now=self.now)
        return qs

    ##################
    ### MANAGEMENT ###
    ##################

    def get_queryset(self):
        try:
            return self._queryset
        except AttributeError:
            self._queryset = self.apply_queryset_rules(self.queryset())\
                                 .distinct()
            return self._queryset

    def run(self):
        """
        Get the queryset, prune sent people, and send it.
        """
        if not self.drip_model.enabled:
            return None

        self.prune()
        count = self.send()

        return count

    def prune(self):
        """
        Do an exclude for all Users who have a SentDrip already.
        """
        target_user_ids = self.get_queryset().values_list('id', flat=True)
        exclude_user_ids = SentDrip.objects.filter(date__lt=conditional_now(),
                                                   drip=self.drip_model,
                                                   user__id__in=target_user_ids)\
                                           .values_list('user_id', flat=True)
        self._queryset = self.get_queryset().exclude(id__in=exclude_user_ids)

    def build_email(self, user, send=False):
        """
        Creates Email instance and optionally sends to user.
        """

        if not self.from_email:
            self.from_email = getattr(settings, 'DRIP_FROM_EMAIL', settings.DEFAULT_FROM_EMAIL)

        email = get_email_instance(self, user).email

        if send:
            sd = SentDrip.objects.create(
                drip=self.drip_model,
                user=user,
                from_email = self.from_email,
                from_email_name = self.from_email_name,
                subject=email.subject,
                body=email.body
            )
            email.send()

        return email

    def send(self):
        """
        Send the email to each user on the queryset.

        Add that user to the SentDrip.

        Returns a list of created SentDrips.
        """

        count = 0
        for user in self.get_queryset():
            msg = self.build_email(user, send=True)
            count += 1

        return count


    ####################
    ### USER DEFINED ###
    ####################

    def queryset(self):
        """
        Returns a queryset of auth.User who meet the
        criteria of the drip.

        Alternatively, you could create Drips on the fly
        using a queryset builder from the admin interface...
        """
        return User.objects
