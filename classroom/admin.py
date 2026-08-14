from django.contrib import admin

from .models import Assignment, StudyMaterial


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "memorandum", "due_date", "created_by", "created_at")
    list_filter = ("due_date", "created_by")
    search_fields = ("title", "instructions")
    autocomplete_fields = ["created_by", "memorandum"]


@admin.register(StudyMaterial)
class StudyMaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "created_by", "created_at")
    search_fields = ("title", "content")
    autocomplete_fields = ["created_by"]
