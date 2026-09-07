import pytest

from codev_platform.ops.systemd_condition_syntax import (
    SystemdConditionSyntaxError,
    UnknownSystemdConditionDirectiveError,
    verify_no_unknown_condition_directives,
)


@pytest.mark.parametrize(
    "content",
    (
        b"[Service]\r\nEnvironment=SAFE=1\r\n",
        b"[Service]\nEnvironment=SAFE=1\r\n",
    ),
)
def test_未知服务配置接受完整CRLF(content: bytes) -> None:
    verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Service]\nEnvironment=SAFE\xe2\x80\xa8ConditionPathExists=/unsafe\n",
        b"[Service]\nEnvironment=SAFE\xe2\x80\xa9AssertPathExists=/unsafe\n",
    ),
)
def test_U2028和U2029不作为物理换行(content: bytes) -> None:
    verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\n\x0bConditionPathExists=/unsafe\n",
        b"[Unit]\n\x0cAssertPathExists=/unsafe\n",
    ),
)
def test_VT和FF不作为键空白(content: bytes) -> None:
    verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\r\nConditionPathExists=/unsafe\r\n",
        b"[Unit]\r\nAssertPathExists=/unsafe\r\n",
    ),
)
def test_CRLF不能隐藏未知条件(content: bytes) -> None:
    with pytest.raises(UnknownSystemdConditionDirectiveError):
        verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\nConditionPathExists=/unsafe\n",
        b"[Unit]\nAssertPathExists=/unsafe\n",
        b"[Unit]\r\nDescription=SAFE\nConditionPathExists=/unsafe\r\n",
        b"[Unit]\nDescription=SAFE\r\nAssertPathExists=/unsafe\n",
    ),
)
def test_LF和合法混合行尾不能隐藏未知条件(content: bytes) -> None:
    with pytest.raises(UnknownSystemdConditionDirectiveError):
        verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\n \t ConditionPathExists \t =/unsafe\n",
        b"[Unit]\n\t AssertPathExists\t =/unsafe\n",
    ),
)
def test_键前后ASCII空格和Tab不能隐藏未知条件(content: bytes) -> None:
    with pytest.raises(UnknownSystemdConditionDirectiveError):
        verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\nconditionPathExists=/safe\n",
        b"[Unit]\ncOnDiTiOnPathExists=/safe\n",
        b"[Unit]\nassertPathExists=/safe\n",
        b"[Unit]\naSsErTPathExists=/safe\n",
    ),
)
def test_条件键名匹配保持大小写敏感(content: bytes) -> None:
    verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\r\n\xef\xbb\xbfConditionPathExists=/unsafe\r\n",
        b"[Unit]\n\xef\xbb\xbfAssertPathExists=/unsafe\n",
        b"[Unit]\n# comment\n\xef\xbb\xbfConditionPathExists=/unsafe\n",
        b"[Unit]\n\xef\xbb\xbf# comment\n",
    ),
)
def test_BOM不能隐藏条件或伪装注释(content: bytes) -> None:
    with pytest.raises(SystemdConditionSyntaxError):
        verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\nCondi\\tionPathExists=/unsafe\n",
        b"[Service]\nEnvironment=SAFE\\VALUE\n",
        b"[Service]\n# SAFE\\COMMENT\n",
        b"[Unit]\\\r\nConditionPathExists=/unsafe\r\n",
    ),
)
def test_反斜杠在任意物理位置都失败关闭(content: bytes) -> None:
    with pytest.raises(SystemdConditionSyntaxError):
        verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Service]\rEnvironment=SAFE=1\n",
        b"[Unit]\r\r\nEnvironment=SAFE=1\r\n",
        b"[Service]\nEnvironment=SAFE=1\x00\n",
        b"[Service]\nEnvironment=\xff\n",
        b"[Unit]\n\xef\xbb\xbfConditionPathExists=/unsafe\n",
    ),
)
def test_不可证明的物理语法失败关闭(content: bytes) -> None:
    with pytest.raises(SystemdConditionSyntaxError):
        verify_no_unknown_condition_directives(content)
