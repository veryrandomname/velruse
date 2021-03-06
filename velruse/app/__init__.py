import os

from anykeystore import create_store_from_settings

from pyramid.config import Configurator
from pyramid.exceptions import ConfigurationError
from pyramid.response import Response

from velruse.app.utils import generate_token
from velruse.app.utils import redirect_form


log = __import__('logging').getLogger(__name__)


def auth_complete_view(context, request):
    endpoint = request.registry.settings.get('endpoint')
    token = generate_token()
    storage = request.registry.velruse_store
    result_data = {
        'provider_type': context.provider_type,
        'provider_name': context.provider_name,
        'profile': context.profile,
        'credentials': context.credentials,
    }
    storage.store(token, result_data, expires=300)
    form = redirect_form(endpoint, token)
    return Response(body=form)


def auth_denied_view(context, request):
    endpoint = request.registry.settings.get('endpoint')
    token = generate_token()
    storage = request.registry.velruse_store
    error_dict = {
        'provider_type': context.provider_type,
        'provider_name': context.provider_name,
        'error': context.reason,
    }
    storage.store(token, error_dict, expires=300)
    form = redirect_form(endpoint, token)
    return Response(body=form)


def auth_info_view(request):
    # TODO: insecure URL, must be protected behind a firewall
    storage = request.registry.velruse_store
    token = request.GET.get('token')
    try:
        return storage.retrieve(token)
    except KeyError:
        log.info('auth_info requested invalid token "%s"')
        request.response.status = 400
        return None


def default_setup(config):
    """Configure Velruse's session factory and backend storage.

    The default setup uses Pyramid's
    ``UnencryptedCookieSessionFactoryConfig`` for storing session data.

    Relevant settings:

    ``session.secret`` controls the secret used when signing the session
    cookies and will be randomly generated if unspecified.

    ``session.cookie_name`` is the name of the cookie stored on a client's
    browser and will default to 'velruse.session'.

    ``store.*`` settings are used by the `anykeystore` library to construct
    a storage backend for user credentials. If no storage settings are
    specified then an in-memory storage backend will be used.

    """
    from pyramid.session import UnencryptedCookieSessionFactoryConfig

    log.info('Using an unencrypted cookie-based session. This can be '
             'changed by pointing the "velruse.setup" setting at a different '
             'function for configuring the session factory.')

    settings = config.registry.settings
    secret = settings.get('session.secret')
    cookie_name = settings.get('session.cookie_name', 'velruse.session')
    if secret is None:
        log.warn('Configuring unencrypted cookie-based session with a '
                 'random secret which will invalidate old cookies when '
                 'restarting the app.')
        secret = ''.join('%02x' % ord(x) for x in os.urandom(16))
        log.info('autogenerated session secret: %s', secret)
    factory = UnencryptedCookieSessionFactoryConfig(
        secret, cookie_name=cookie_name)
    config.set_session_factory(factory)

    # setup backing storage
    storage_string = settings.get('store', 'memory')
    settings['store.store'] = storage_string
    store = create_store_from_settings(settings, prefix='store.')
    config.register_velruse_store(store)


def register_velruse_store(config, storage):
    """Add key/value store for Velruse to the Pyramid application.

    This function is registered with Pyramid and can be used via
    ``config.register_velruse_store(storage)``.

    ``storage`` should be an instance of an `anykeystore` backend.

    """
    config.registry.velruse_store = storage


settings_adapter = {
    'bitbucket': 'add_bitbucket_login_from_settings',
    'douban': 'add_douban_login_from_settings',
    'facebook': 'add_facebook_login_from_settings',
    'github': 'add_github_login_from_settings',
    'google': 'add_google_login_from_settings',
    'google_oauth2': 'add_google_oauth2_login_from_settings',
    'lastfm': 'add_lastfm_login_from_settings',
    'linkedin': 'add_linkedin_login_from_settings',
    'live': 'add_live_login_from_settings',
    'qq': 'add_qq_login_from_settings',
    'renren': 'add_renren_login_from_settings',
    'taobao': 'add_taobao_login_from_settings',
    'twitter': 'add_twitter_login_from_settings',
    'weibo': 'add_weibo_login_from_settings',
    'openid': 'add_openid_login_from_settings',
    'yahoo': 'add_yahoo_login_from_settings',
}


def find_providers(settings):
    providers = set()
    for k in settings:
        if k.startswith('provider.'):
            k = k[9:].split('.', 1)[0]
            providers.add(k)
    return providers


def load_provider(config, provider):
    settings = config.registry.settings
    impl = settings.get('provider.%s.impl' % provider) or provider

    login_cfg = settings_adapter.get(impl)
    if login_cfg is None:
        raise ConfigurationError(
            'could not find configuration method for provider %s'
            '' % provider)
    loader = getattr(config, login_cfg)
    loader(prefix='provider.%s.' % provider)


def includeme(config):
    """Add the Velruse standalone app configuration to a Pyramid app."""
    settings = config.registry.settings
    config.add_directive('register_velruse_store', register_velruse_store)

    # setup application
    setup = settings.get('setup') or default_setup
    if setup:
        config.include(setup)

    # include supported providers
    for provider in settings_adapter:
        config.include('velruse.providers.%s' % provider)

    # configure requested providers
    for provider in find_providers(settings):
        load_provider(config, provider)

    # check for required settings
    if not settings.get('endpoint'):
        raise ConfigurationError(
            'missing required setting "endpoint"')

    # add views
    config.add_view(
        auth_complete_view,
        context='velruse.AuthenticationComplete')
    config.add_view(
        auth_denied_view,
        context='velruse.AuthenticationDenied')
    config.add_view(
        auth_info_view,
        name='auth_info',
        request_param='format=json',
        renderer='json')


def make_app(global_conf, **settings):
    """Construct a complete WSGI app.

    This function is compatible with the `PasteDeploy` WSGI application factory
    API, which is also used by Pyramid's ``pserve`` script.

    Example INI file:

    .. code-block:: ini

        [server:main]
        use = egg:Paste#http
        host = 0.0.0.0
        port = 80

        [composite:main]
        use = egg:Paste#urlmap
        / = YOURAPP
        /velruse = velruse

        [app:velruse]
        use = egg:velruse

        setup = myapp.setup_velruse

        endpoint = http://example.com/logged_in

        store = redis
        store.host = localhost
        store.port = 6379
        store.db = 0
        store.key_prefix = velruse_ustore

        provider.facebook.consumer_key = KMfXjzsA2qVUcnnRn3vpnwWZ2pwPRFZdb
        provider.facebook.consumer_secret =
            ULZ6PkJbsqw2GxZWCIbOEBZdkrb9XwgXNjRy
        provider.facebook.scope = email

        provider.tw.impl = twitter
        provider.tw.consumer_key = ULZ6PkJbsqw2GxZWCIbOEBZdkrb9XwgXNjRy
        provider.tw.consumer_secret = eoCrFwnpBWXjbim5dyG6EP7HzjhQzFsMAcQOEK

        [app:YOURAPP]
        use = egg:YOURAPP
        full_stack = true
        static_files = true

    """
    config = Configurator(settings=settings)
    config.include(includeme)
    return config.make_wsgi_app()
