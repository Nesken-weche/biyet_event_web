from django.urls import path

from . import views, views_account as acct

urlpatterns = [
    # Dashboard tabs
    path("", views.events_tab, name="events"),
    path("discover/", views.discover_tab, name="discover"),
    path("history/", views.history_tab, name="history"),
    path("swap/", acct.swap_tab, name="swap"),
    # Events + cart
    path("events/<str:event_id>/", views.event_detail, name="event_detail"),
    path("events/<str:event_id>/add/", views.add_to_cart, name="add_to_cart"),
    path("events/<str:event_id>/report/", acct.report_event, name="report_event"),
    path("cart/", views.cart, name="cart"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("cart/checkout/", views.checkout_start, name="checkout_start"),
    path("cart/checkout/complete/", views.checkout_complete, name="checkout_complete"),
    path("cart/checkout/cancel/", views.checkout_cancel, name="checkout_cancel"),
    # Tickets
    path("tickets/<str:ticket_id>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<str:ticket_id>/status/", views.ticket_status, name="ticket_status"),
    # Swap
    path("swap/new/", acct.swap_new, name="swap_new"),
    path("swap/<str:swap_id>/action/", acct.swap_action, name="swap_action"),
    # Drawer
    path("me/", acct.menu, name="menu"),
    path("notifications/", acct.notifications, name="notifications"),
    path("notifications/read/", acct.notif_read, name="notif_read"),
    path("rewards/", acct.rewards, name="rewards"),
    path("settings/", acct.settings_view, name="settings"),
    path("profile/", acct.profile, name="profile"),
    path("me/delete/", acct.delete_account, name="delete_account"),
    path("me/device-token/", acct.device_token, name="device_token"),
    # Auth
    path("login/", acct.login_view, name="login"),
    path("signup/", acct.signup_view, name="signup"),
    path("auth/firebase/", acct.firebase_session, name="firebase_session"),
    path("auth/signup/", acct.signup_create, name="signup_create"),
    path("verify/", acct.verify_code, name="verify_code"),
    path("logout/", acct.logout_view, name="logout"),
    path("demo-login/", acct.demo_login, name="demo_login"),
    # PWA
    path("manifest.webmanifest", acct.manifest, name="manifest"),
    path("sw.js", acct.service_worker, name="service_worker"),
    path("offline/", acct.offline, name="offline"),
    path("favicon.ico", acct.favicon, name="favicon"),
]