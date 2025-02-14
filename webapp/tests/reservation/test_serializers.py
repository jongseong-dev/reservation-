import pytest
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from reservation.const import (
    MAXIMUM_RESERVED_COUNT,
    ReservationErrorResponseMessage,
    DAYS_PRIOR_TO_RESERVATION,
)
from reservation.factories import ExamScheduleFactory, ReservationFactory
from reservation.models import Reservation
from reservation.serializers import (
    ExamScheduleListSerializer,
    ReservationCreateUpdateSerializer,
    AdminReservationUpdateStatusSerializer,
    ReservationDeleteSerializer,
)


@pytest.fixture
def exam_schedule():
    offset = timezone.timedelta(days=DAYS_PRIOR_TO_RESERVATION + 1)
    return ExamScheduleFactory.create(
        start_datetime=timezone.now() + offset,
        max_capacity=10,
        confirmed_reserved_count=0,
    )


@pytest.fixture
def reservation_create_serializer():
    return ReservationCreateUpdateSerializer()


@pytest.fixture
def reservation(user, exam_schedule):
    return ReservationFactory.create(
        user=user,
        exam_schedule=exam_schedule,
        status=Reservation.Status.PENDING,
        reserved_count=2,
    )


@pytest.mark.django_db
def test_exam_schedule_list_serializer_correct_remain_count(
    exam_schedule,
):
    serializer = ExamScheduleListSerializer(exam_schedule)
    remain_count = serializer.get_remain_count(
        {"max_capacity": 50000, "confirmed_reserved_count": 10000}
    )
    assert remain_count == 40000


@pytest.mark.django_db
def test_reservation_create_serializer_validates_correctly(exam_schedule):
    serializer = ReservationCreateUpdateSerializer(
        data={
            "exam_schedule_id": exam_schedule.id,
            "reserved_count": 10,
        }
    )
    assert serializer.is_valid()


@pytest.mark.django_db
def test_reservation_create_serializer_raises_error_for_exceeding_capacity(
    exam_schedule,
):
    serializer = ReservationCreateUpdateSerializer(
        data={
            "exam_schedule_id": exam_schedule.id,
            "reserved_count": MAXIMUM_RESERVED_COUNT + 300,
        }
    )
    with pytest.raises(serializers.ValidationError):
        serializer.is_valid(raise_exception=True)


@pytest.mark.django_db
def test_reservation_create_serializer_raises_error_non_id(
    exam_schedule,
):
    serializer = ReservationCreateUpdateSerializer(
        data={"exam_schedule_id": exam_schedule.id + 10, "reserved_count": 10}
    )
    with pytest.raises(serializers.ValidationError):
        serializer.is_valid(raise_exception=True)


@pytest.fixture
def serializer(reservation):
    return AdminReservationUpdateStatusSerializer(instance=reservation)


@pytest.mark.django_db
def test_update_to_reserved_status(serializer, reservation, exam_schedule):
    data = {"status": Reservation.Status.RESERVED}
    updated_reservation = serializer.update(reservation, data)

    assert updated_reservation.status == Reservation.Status.RESERVED


@pytest.mark.django_db
def test_update_already_reserved_status(serializer, reservation):
    reservation.status = Reservation.Status.CANCLED
    reservation.save()

    data = {"status": Reservation.Status.CANCLED}
    expected_result = ReservationErrorResponseMessage.SAME_STATUS_CHECK
    with pytest.raises(serializers.ValidationError) as exc_info:
        serializer.update(reservation, data)
    assert str(exc_info.value.detail[0]) == expected_result


@pytest.mark.django_db
@pytest.mark.parametrize(
    "initial_status,new_status",
    [
        (Reservation.Status.PENDING, Reservation.Status.CANCLED),
        (Reservation.Status.CANCLED, Reservation.Status.PENDING),
    ],
)
def test_update_other_status_changes(
    serializer, reservation, initial_status, new_status
):
    reservation.status = initial_status
    reservation.save()

    data = {"status": new_status}
    updated_reservation = serializer.update(reservation, data)

    assert updated_reservation.status == new_status
    assert updated_reservation.reserved_count == 2


@pytest.mark.django_db
def test_serializer_output(serializer, reservation, user, exam_schedule):
    data = serializer.data
    assert data["id"] == reservation.id
    assert data["reserved_user_email"] == user.email
    assert data["reserved_username"] == user.username
    assert data["reserved_count"] == reservation.reserved_count
    assert data["status"] == reservation.status
    assert "exam_schedule" in data


# 추가 테스트: RESERVED 상태에서 다른 상태로 변경 불가
@pytest.mark.django_db
def test_update_from_reserved_to_other_status(serializer, reservation):
    reservation.status = Reservation.Status.RESERVED
    reservation.save()
    expected_result = ReservationErrorResponseMessage.CAN_NOT_MODIFY_RESERVED
    data = {"status": Reservation.Status.PENDING}
    with pytest.raises(serializers.ValidationError) as exc_info:
        serializer.update(reservation, data)
    assert str(exc_info.value.detail[0]) == expected_result


@pytest.mark.django_db
def test_valid_data(reservation_create_serializer, exam_schedule):
    data = {"exam_schedule_id": exam_schedule.id, "reserved_count": 5}
    assert reservation_create_serializer.validate(data) == data


@pytest.mark.django_db
def test_exam_schedule_not_found(reservation_create_serializer):
    data = {"exam_schedule_id": 9999, "reserved_count": 5}  # Non-existent ID
    with pytest.raises(ValidationError) as exc_info:
        reservation_create_serializer.validate(data)
    expected_result = ReservationErrorResponseMessage.NOT_FOUND_EXAM_SCHEDULE
    assert str(exc_info.value.detail[0]) == expected_result


@pytest.mark.django_db
def test_exam_schedule_too_soon(reservation_create_serializer, exam_schedule):
    exam_schedule.start_datetime = timezone.now() + timezone.timedelta(
        days=DAYS_PRIOR_TO_RESERVATION - 1
    )
    exam_schedule.save()

    data = {"exam_schedule_id": exam_schedule.id, "reserved_count": 5}
    with pytest.raises(ValidationError) as exc_info:
        reservation_create_serializer.validate(data)
    expected_result = ReservationErrorResponseMessage.ALREADY_DAYS_AGO_RESERVED
    assert str(exc_info.value.detail[0]) == expected_result


@pytest.mark.django_db
def test_exceed_remain_count(reservation_create_serializer, exam_schedule):
    exam_schedule.confirmed_reserved_count = 8
    exam_schedule.save()

    data = {"exam_schedule_id": exam_schedule.id, "reserved_count": 5}
    with pytest.raises(ValidationError) as exc_info:
        reservation_create_serializer.validate(data)
    expected_result = ReservationErrorResponseMessage.EXCEED_REMAIN_COUNT
    assert str(exc_info.value.detail[0]) == expected_result


@pytest.mark.django_db
def test_exact_remain_count(reservation_create_serializer, exam_schedule):
    exam_schedule.confirmed_reserved_count = 5
    exam_schedule.save()

    data = {"exam_schedule_id": exam_schedule.id, "reserved_count": 5}
    assert reservation_create_serializer.validate(data) == data


@pytest.mark.parametrize("reserved_count", [0, -1])
@pytest.mark.django_db
def test_invalid_reserved_count(
    reservation_create_serializer, exam_schedule, reserved_count
):
    data = {
        "exam_schedule_id": exam_schedule.id,
        "reserved_count": reserved_count,
    }
    with pytest.raises(ValidationError):
        ReservationCreateUpdateSerializer(data=data).is_valid(
            raise_exception=True
        )


@pytest.mark.django_db
def test_create_reservation(user, exam_schedule):
    data = {"exam_schedule_id": exam_schedule.id, "reserved_count": 5}
    serializer = ReservationCreateUpdateSerializer(data=data)
    assert serializer.is_valid()
    reservation = serializer.save(user=user)
    assert reservation.exam_schedule == exam_schedule
    assert reservation.reserved_count == 5


@pytest.mark.django_db
def test_update_reservation_status_when_status_is_reserved(reservation):
    reservation.status = Reservation.Status.RESERVED
    serializer = ReservationDeleteSerializer(instance=reservation)

    with pytest.raises(ValidationError):
        serializer.update(reservation, {"status": Reservation.Status.CANCLED})


@pytest.mark.django_db
def test_update_reservation_status_when_status_is_not_reserved(reservation):
    reservation.status = Reservation.Status.PENDING
    serializer = ReservationDeleteSerializer(
        instance=reservation, data={"status": Reservation.Status.CANCLED}
    )
    update_serializer = serializer.update(
        reservation, {"status": Reservation.Status.CANCLED}
    )
    assert update_serializer.status == Reservation.Status.CANCLED
