from rest_framework import serializers

from uploads.models import BulkImageImport, BulkImageImportGroup, BulkImageImportItem


class BulkImageImportItemSerializer(serializers.ModelSerializer):
    preview_path = serializers.SerializerMethodField()

    class Meta:
        model = BulkImageImportItem
        fields = ["item_id", "source_index", "checksum_sha256", "detected_format", "width", "height", "decision", "safe_issue_code", "preview_path"]

    def get_preview_path(self, obj):
        if (
            not obj.staged_object_key
            or obj.decision in {"skipped", "invalid"}
            or obj.group.bulk_import.status != "preview"
        ):
            return ""
        return f"/api/uploads/bulk-imports/{obj.group.bulk_import.import_id}/items/{obj.item_id}/preview/"


class BulkImageImportGroupSerializer(serializers.ModelSerializer):
    items = BulkImageImportItemSerializer(many=True, read_only=True)
    encounter = serializers.SerializerMethodField()

    class Meta:
        model = BulkImageImportGroup
        fields = ["group_id", "source_index", "mrn", "assessment_date", "status", "safe_issue_code", "encounter", "items"]

    def get_encounter(self, obj):
        encounter = obj.resolved_encounter or obj.proposed_encounter
        if not encounter:
            return None
        patient = encounter.patient
        return {
            "id": encounter.id,
            "encounter_id": encounter.encounter_id,
            "patient_name": f"{patient.first_name} {patient.last_name}".strip(),
            "sentinel_patient_id": getattr(patient.master_patient, "sentinel_patient_id", "") if patient.master_patient_id else "",
        }


class BulkImageImportSerializer(serializers.ModelSerializer):
    groups = BulkImageImportGroupSerializer(many=True, read_only=True)

    class Meta:
        model = BulkImageImport
        fields = ["import_id", "service_session", "branch", "status", "image_count", "skipped_count", "safe_error_code", "created_at", "expires_at", "confirmed_at", "groups"]
