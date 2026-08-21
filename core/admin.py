from django.contrib import admin

from .models import School, TeacherInvite


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "grade_range_label", "created_by", "created_at")
    search_fields = ("name",)
    readonly_fields = ("student_join_code", "parent_student_join_code", "created_at")


@admin.register(TeacherInvite)
class TeacherInviteAdmin(admin.ModelAdmin):
    list_display = ("teacher_name", "school", "assigned_grades", "code", "is_claimed")
    list_filter = ("school",)
    search_fields = ("teacher_name", "code", "school__name")
    readonly_fields = ("code", "claimed_by", "claimed_at", "created_at")

    @admin.display(boolean=True, description="Claimed")
    def is_claimed(self, obj):
        return obj.is_claimed
