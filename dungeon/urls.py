from django.urls import path

from . import views

app_name = "dungeon"

urlpatterns = [
    path("", views.launch, name="launch"),
    path("start/", views.start_run, name="start_run"),
    path("run/<int:pk>/", views.room, name="room"),
    path("run/<int:pk>/state/", views.state, name="state"),
    path("run/<int:pk>/move/", views.move, name="move"),
    path("run/<int:pk>/answer/", views.answer, name="answer"),
    path("run/<int:pk>/item/", views.use_item, name="use_item"),
    path("run/<int:pk>/exit/", views.exit_room, name="exit_room"),
    path("run/<int:pk>/abandon/", views.abandon, name="abandon"),
    path("run/<int:pk>/summary/", views.summary, name="summary"),
]
