from django.core.validators import MinValueValidator
from django.db import transaction
from django.db.models import F
from rest_framework import serializers

from reservation.const import (
    ReservationErrorResponseMessage,
    DAYS_PRIOR_TO_RESERVATION,
)
from reservation.models import Reservation, ExamSchedule
from reservation.validate import validate_reservation


class ExamScheduleListSerializer(serializers.ModelSerializer):
    start_datetime = serializers.DateTimeField(
        format="%Y-%m-%dT%H:00:00%z", help_text="시험 시작 일시"
    )
    end_datetime = serializers.DateTimeField(
        format="%Y-%m-%dT%H:00:00%z", help_text="시험 끝 일시"
    )
    remain_count = serializers.SerializerMethodField(
        help_text="남은 예약 가능 인원"
    )
    is_available = serializers.SerializerMethodField(
        help_text="예약 가능 여부"
    )

    class Meta:
        model = ExamSchedule
        fields = [
            "id",
            "start_datetime",
            "end_datetime",
            "remain_count",
            "is_available",
        ]

    def get_remain_count(self, obj) -> int:
        if isinstance(obj, dict):
            return obj["max_capacity"] - obj["confirmed_reserved_count"]
        return obj.max_capacity - obj.confirmed_reserved_count

    def get_is_available(self, obj) -> bool:
        return self.get_remain_count(obj) > 0


class ReservationSerializer(serializers.ModelSerializer):
    exam_schedule = ExamScheduleListSerializer(read_only=True)
    status = serializers.CharField(read_only=True)

    class Meta:
        model = Reservation
        fields = ["id", "exam_schedule", "reserved_count", "status"]


class ReservationDeleteSerializer(serializers.ModelSerializer):
    status = serializers.CharField(write_only=True)

    class Meta:
        model = Reservation
        fields = ["status"]

    def update(self, instance, validated_data):
        if instance.status == Reservation.Status.RESERVED:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.CAN_NOT_MODIFY_RESERVED
            )
        return super().update(instance, validated_data)


class ReservationCreateUpdateSerializer(serializers.ModelSerializer):
    exam_schedule_id = serializers.IntegerField(help_text="시험 일정 ID")
    reserved_count = serializers.IntegerField(
        validators=[
            MinValueValidator(1),
        ],
        help_text="예약 인원 수",
    )

    class Meta:
        model = Reservation
        fields = [
            "id",
            "exam_schedule_id",
            "reserved_count",
        ]

    def validate(self, data):
        exam_scheduled_id = data["exam_schedule_id"]
        reserved_count = data["reserved_count"]
        try:
            exam_schedule = ExamSchedule.objects.get(id=exam_scheduled_id)
        except ExamSchedule.DoesNotExist:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.NOT_FOUND_EXAM_SCHEDULE
            )

        validate_reservation(
            exam_schedule, reserved_count, DAYS_PRIOR_TO_RESERVATION
        )

        return data

    def create(self, validated_data):
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if instance.status == Reservation.Status.RESERVED:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.CAN_NOT_MODIFY_RESERVED
            )
        return super().update(instance, validated_data)


class AdminReservationSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)
    reserved_user_email = serializers.SerializerMethodField(
        help_text="예약자 이메일"
    )
    reserved_username = serializers.SerializerMethodField(
        help_text="예약자 이름"
    )
    exam_schedule = ExamScheduleListSerializer(read_only=True)
    exam_schedule_id = serializers.IntegerField(write_only=True)

    def get_reserved_user_email(self, obj) -> str:
        return obj.user.email

    def get_reserved_username(self, obj) -> str:
        return obj.user.username

    class Meta:
        model = Reservation
        fields = [
            "id",
            "exam_schedule",
            "exam_schedule_id",
            "reserved_count",
            "reserved_user_email",
            "reserved_username",
            "status",
        ]

    def validate(self, data):
        exam_scheduled_id = data["exam_schedule_id"]
        try:
            exam_schedule = ExamSchedule.objects.get(id=exam_scheduled_id)
        except ExamSchedule.DoesNotExist:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.NOT_FOUND_EXAM_SCHEDULE
            )

        validate_reservation(
            exam_schedule, data["reserved_count"], DAYS_PRIOR_TO_RESERVATION
        )

        return data

    def update(self, instance, validated_data):
        if instance.status == Reservation.Status.RESERVED:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.CAN_NOT_MODIFY_RESERVED
            )

        return super().update(instance, validated_data)


class AdminReservationUpdateStatusSerializer(serializers.ModelSerializer):
    exam_schedule = ExamScheduleListSerializer(read_only=True)
    reserved_user_email = serializers.SerializerMethodField(
        help_text="예약자 이메일"
    )
    reserved_username = serializers.SerializerMethodField(
        help_text="예약자 이름"
    )
    status = serializers.CharField(read_only=True)
    reserved_count = serializers.IntegerField(read_only=True)

    def get_reserved_user_email(self, obj) -> str:
        return obj.user.email

    def get_reserved_username(self, obj) -> str:
        return obj.user.username

    class Meta:
        model = Reservation
        fields = [
            "id",
            "exam_schedule",
            "reserved_user_email",
            "reserved_username",
            "reserved_count",
            "exam_schedule",
            "status",
        ]

    def _validate_reserved_count(self):
        # 예약 가능 인원수를 초과했는지 확인
        exam_schedule = self.instance.exam_schedule
        reserved_count = self.instance.reserved_count
        current_count = reserved_count + exam_schedule.confirmed_reserved_count
        if current_count > exam_schedule.max_capacity:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.EXCEED_REMAIN_COUNT
            )

    def validate_status(self, status):
        if self.is_same_status(self.instance, status):
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.SAME_STATUS_CHECK
            )
        if self.instance.status == Reservation.Status.RESERVED:
            raise serializers.ValidationError(
                ReservationErrorResponseMessage.CAN_NOT_MODIFY_RESERVED
            )

    def is_same_status(self, instance, status):
        return instance.status == status

    def update(self, instance, validated_data):
        status = validated_data["status"]
        self.validate_status(status)
        self._validate_reserved_count()
        with transaction.atomic():
            instance.status = status
            instance.reserved_count = validated_data.get(
                "reserved_count", instance.reserved_count
            )
            instance.save()

            if status == Reservation.Status.RESERVED:
                exam_schedule = instance.exam_schedule
                exam_schedule.confirmed_reserved_count = (
                    F("confirmed_reserved_count") + instance.reserved_count
                )
                exam_schedule.save()
                exam_schedule.refresh_from_db()
            return instance
