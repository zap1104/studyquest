from django.contrib import admin
from .models import (
    Chapter,
    Choice,
    Course,
    CourseTopic,
    Question,
    QuestionTopic,
    Quiz,
    UserProfile,
    XPTransaction,
    QuizAttempt,
    ChapterCompletion,
    StartingKnowledgeCheck,
)

class ChapterInline(admin.TabularInline):
    model = Chapter
    extra = 0

class CourseTopicInline(admin.TabularInline):
    model = CourseTopic
    extra = 0

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'created_at')
    inlines = [ChapterInline, CourseTopicInline]

class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0

class QuestionTopicInline(admin.TabularInline):
    model = QuestionTopic
    extra = 0

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('text', 'quiz')
    inlines = [ChoiceInline, QuestionTopicInline]

@admin.register(CourseTopic)
class CourseTopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'key', 'course', 'order', 'created_at')
    list_filter = ('course',)
    search_fields = ('name', 'key', 'course__title')

@admin.register(QuestionTopic)
class QuestionTopicAdmin(admin.ModelAdmin):
    list_display = ('question', 'topic', 'created_at')
    list_filter = ('topic__course',)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'current_level', 'total_xp', 'streak_days', 'last_study_date')

@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'quiz', 'score', 'total_questions', 'xp_earned', 'completed_at')
    list_filter = ('completed_at',)

@admin.register(XPTransaction)
class XPTransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'reason', 'created_at')
    list_filter = ('created_at',)

@admin.register(ChapterCompletion)
class ChapterCompletionAdmin(admin.ModelAdmin):
    list_display = ('user', 'chapter', 'completed_at')

@admin.register(StartingKnowledgeCheck)
class StartingKnowledgeCheckAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'status', 'score', 'total_questions', 'focus_adopted', 'created_at')
    list_filter = ('status', 'focus_adopted')

admin.site.register(Chapter)
admin.site.register(Quiz)