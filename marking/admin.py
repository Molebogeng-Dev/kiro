"""Admin for memorandums and marked papers.

Memorandum authoring lives here for this sprint, so a teacher-facing authoring
UI is not on the critical path. The Paper and result views are read-mostly: they
exist so a failed marking run can be diagnosed without a database client.
"""

from django.contrib import admin

from .models import Memorandum, MarkingResult, Paper, QuestionResult


@admin.register(Memorandum)
class MemorandumAdmin(admin.ModelAdmin):
    list_display = ("title", "subject", "total_marks", "created_by", "created_at")
    list_filter = ("subject",)
    search_fields = ("title", "content")
    autocomplete_fields = ["created_by"]

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class QuestionResultInline(admin.TabularInline):
    model = QuestionResult
    extra = 0
    fields = ("number", "marks_awarded", "marks_available", "feedback")


@admin.register(MarkingResult)
class MarkingResultAdmin(admin.ModelAdmin):
    list_display = ("paper", "marks_awarded", "marks_available", "model_used", "created_at")
    list_filter = ("model_used",)
    inlines = [QuestionResultInline]
    readonly_fields = ("raw_response",)


@admin.register(Paper)
class PaperAdmin(admin.ModelAdmin):
    list_display = ("id", "memorandum", "submitted_by", "status", "failure_kind", "created_at")
    list_filter = ("status", "failure_kind")
    search_fields = ("submitted_by__username", "memorandum__title")
    autocomplete_fields = ["submitted_by", "memorandum"]
    readonly_fields = ("created_at", "updated_at", "failure_detail")
