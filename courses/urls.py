from django.urls import path
from . import views

app_name = "courses"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("courses/", views.course_list, name="course_list"),
    path("courses/new/", views.course_create, name="course_create"),
    path("courses/<int:pk>/", views.course_detail, name="course_detail"),
    path("<int:pk>/edit/", views.course_edit, name="course_edit"),
    path("<int:pk>/delete/", views.course_delete, name="course_delete"),
    path("<int:pk>/archive/", views.course_archive, name="course_archive"),
    path("<int:pk>/restore/", views.course_restore, name="course_restore"),
    path("chapters/<int:pk>/review/", views.chapter_review, name="chapter_review"),
    path("chapters/<int:pk>/reading-complete/", views.record_chapter_reading, name="record_chapter_reading"),
    path("chapters/<int:pk>/complete/", views.complete_chapter, name="complete_chapter"),
    path("chapters/<int:pk>/rename/", views.chapter_rename, name="chapter_rename"),
    path("chapters/<int:pk>/quiz/", views.chapter_quiz, name="chapter_quiz"),
    path("chapters/<int:pk>/quiz/check/", views.check_quiz_answer, name="check_quiz_answer"),
    path("chapters/<int:pk>/quiz/submit/", views.submit_quiz, name="submit_quiz"),
    path("profile/", views.profile_view, name="profile"),
    path("leaderboard/", views.leaderboard_view, name="leaderboard"),
]