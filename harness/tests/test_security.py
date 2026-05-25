from policies.security import ShellPolicy, FilePolicy, WebPolicy, DockerPolicy
from tools.registry import ToolRegistry


def test_blocks_sudo():
    assert not ShellPolicy().validate('sudo reboot').allowed


def test_blocks_shell_chaining():
    assert not ShellPolicy().validate('ls; cat /etc/passwd').allowed


def test_blocks_root_delete():
    assert not ShellPolicy().validate('rm -rf /').allowed


def test_allows_pytest():
    assert ShellPolicy().validate('pytest -q').allowed


def test_guest_cannot_shell():
    try:
        ToolRegistry().execute('shell', command='pwd')
    except PermissionError:
        return
    assert False


def test_guest_tools_are_limited():
    assert 'shell' not in ToolRegistry().visible_tools(None)
    assert 'financial' in ToolRegistry().visible_tools(None)


def test_blocks_file_outside_roots():
    try:
        FilePolicy().resolve('/etc/passwd')
    except PermissionError:
        return
    assert False


def test_blocks_external_host():
    try:
        WebPolicy().validate('https://example.com')
    except PermissionError:
        return
    assert False


def test_trader_cannot_restart():
    try:
        DockerPolicy().validate('restart','quantlab_harness','trader')
    except PermissionError:
        return
    assert False
