"""Command-Line Lint --- lint your command-line history.
Copyright (c) 2018, 2019 Chris Rayner (dchrisrayner@gmail.com).
This software is licensed under the permissive MIT License.

This script generates a simple report against your command-line history and
suggests workflow improvements.  It has the opinion that most of the commands
you type should be simple and require minimal typing.  The report will contain:

- comprehensive lists of commands you use, with and without arguments
- suggestions for ways to shorten commands (aliases, alternative syntax)
- a subset Shellcheck lints (if it's installed); many of these are
  useful and can warn against dangerous habits

This code is an early prototype and currently has weak support for shells
besides bash.

"""
import re
import os
import stat
import sys
import difflib
from subprocess import check_output, CalledProcessError
import distutils.spawn
from collections import Counter

# these parametrize the length and format of the report:
NUM_COMMANDS = 5
NUM_WITH_ARGUMENTS = 10
NUM_SHELLCHECK = 10
ENV_INDENT = 13

NO_COLOR = os.environ.get('NO_COLOR')  # https://no-color.org
COLOR_DEFAULT = '' if NO_COLOR else '\033[0m'
COLOR_HEADER = '' if NO_COLOR else '\033[7m'
COLOR_WARN = '' if NO_COLOR else '\033[31m'
COLOR_TIP = '' if NO_COLOR else '\033[32m'

# shellcheck errors and warnings that are not really relevant:
SC_IGNORE = [1089, 1090, 1091, 2086, 2103, 2148, 2154, 2164, 2224, 2230]


def report_environment():
    """Report on some common environment settings."""
    _print_header("Environment", newline=False)
    _print_environment_variable('SHELL')
    _print_environment_variable('HISTFILE')
    _lint_histfile()
    _print_environment_variable('HISTSIZE')
    _print_environment_variable('HISTFILESIZE')
    _lint_histsize()
    _print_environment_variable('HISTCONTROL')
    _lint_histcontrol()
    _print_environment_variable('HISTIGNORE')
    _lint_histignore()
    _lint_histappend()


def report_favorites(commands, top_n=NUM_COMMANDS):
    """Report for user's {top_n} most common commands."""
    _print_header("Favorite {}".format(top_n), newline=False)
    prefix_count = Counter(cmd.split()[0] for cmd in commands if ' ' in cmd)
    for prefix, count in prefix_count.most_common(top_n):
        _print_command_stats(prefix, count, len(commands))


def report_commands_with_arguments(commands, top_n=NUM_WITH_ARGUMENTS):
    """Report for user's {top_n} most common commands (with args)."""
    _print_header("Top {} with args".format(top_n))
    for cmd, count in Counter(commands).most_common(top_n):
        _print_command_stats(cmd, count, len(commands))
        if not _is_in_histignore(cmd):
            any(
                lint(cmd, count, len(commands)) for lint in [
                    _lint_alias,
                    _lint_add_to_histignore,
                ])


def report_miscellaneous(commands):
    """Report for some miscellaneous ways to reduce typing."""
    _print_header('Command length')
    print("Your commands tend to be {} chars long with {} argument(s).".format(
        int(sum(len(cmd) for cmd in commands) / len(commands)),
        int(sum(len(cmd.split()) - 1 for cmd in commands) / len(commands))))
    for lint in [
            _lint_rename,
            _lint_cd_home,
    ]:
        any(lint(cmd) for cmd in set(commands))


def report_shellcheck(history_file, top_n=NUM_SHELLCHECK):
    """Report containing lints from 'Shellcheck'."""
    _print_header('Shellcheck')
    if not _is_shellcheck_installed():
        print('Shellcheck not installed - see https://www.shellcheck.net')
        return
    try:
        args = [
            "--exclude={}".format(','.join(str(cc) for cc in SC_IGNORE)),
            "--shell={}".format(_shell()),
        ]
        check_output(['shellcheck', args[0], args[1], history_file])
        print('Nothing to report.')
        return
    except CalledProcessError as err:
        # non-zero exit status means we may have found some warnings
        shellcheck_errors = err.output.decode('utf-8').strip().split('\n\n')
    old_errors = set()
    for error in shellcheck_errors:
        errors = (cc for cc in re.findall(r"SC([0-9]{4}):", error))
        new_errors = [cc for cc in errors if cc not in old_errors][:top_n]
        if new_errors:
            old_errors = old_errors.union(new_errors)
            print(
                re.sub(
                    r'(\^-- .*)',
                    "{}\\1{}".format(COLOR_TIP, COLOR_DEFAULT),
                    _remove_prefix(error.strip(), r'In .* line .*:\n'),
                ))


def _tip(tip, indent=0):
    print(' ' * indent + "{}^-- {}{}".format(COLOR_TIP, tip, COLOR_DEFAULT))


def _warn(warn):
    print("{}WARNING: {}{}".format(COLOR_WARN, warn, COLOR_DEFAULT))


def _print_header(header, newline=True):
    if newline:
        print()
    print(COLOR_HEADER + '{} '.format(header).ljust(79) + COLOR_DEFAULT)


def _print_environment_variable(var):
    print("{}=> {}".format(
        var.ljust(ENV_INDENT),
        os.environ.get(var, '<default>'),
    ))


def _print_command_stats(cmd, count, total):
    cmd = cmd.ljust(39)
    percent = "{}%".format(round(100 * count / total, 1)).rjust(20)
    times = "{}/{}".format(count, total).rjust(20)
    print("{}{}{}".format(cmd, percent, times))


def _lint_add_to_histignore(cmd, count, total):
    if len(cmd) >= 4 or count < 2 or total / count > 20:
        return False
    _tip("Ignore short commands with HISTIGNORE={}:$HISTIGNORE".format(cmd))
    return True


def _lint_alias(cmd, count, total):
    if (cmd in str(check_output([_shell(), '-i', '-c', 'alias'])) or count < 2
            or total / count > 20 or ' ' not in cmd):
        return False
    suggestion = ''.join(
        word[0] for word in cmd.split() if re.match(r'\w', word))
    _tip('Consider using an alias: alias {}="{}"'.format(suggestion, cmd))
    return True


def _lint_cd_home(cmd):
    if _standardize(cmd) in {'cd ~', 'cd ~/', 'cd $HOME'}:
        print(cmd)
        _tip('Useless argument.  Use "cd"', indent=3)
        return True
    return False


def _lint_histappend():
    shell = os.environ.get('SHELL')
    if not shell.endswith('bash'):
        return False
    histappend = str(check_output([shell, '-i', '-c', 'shopt histappend']))
    if r'\ton\n' not in histappend:
        _tip('Add "shopt -s histappend" to .bashrc to retain more history')
        return True
    return False


def _lint_histcontrol():
    histcontrol = os.environ.get('HISTCONTROL', '')
    if ('ignoredups' in histcontrol or 'erasedups' in histcontrol):
        _tip(
            'This removes duplicates; unset these for better reporting',
            indent=ENV_INDENT + 3,
        )
        return True
    return False


def _lint_histignore():
    if not os.environ.get('HISTIGNORE'):
        _tip(
            'Consider adding short commands like "ls" to your HISTIGNORE',
            indent=ENV_INDENT + 3,
        )
        return True
    return False


def _lint_histfile():
    history_file = _history_file()
    if os.stat(history_file).st_mode & stat.S_IROTH:
        _tip(
            "Other users can read {}!".format(history_file),
            indent=ENV_INDENT + 3,
        )
        return True
    return False


def _lint_histsize():
    if (int(os.environ.get('HISTSIZE', '500')) < 5000
            or int(os.environ.get('HISTFILESIZE', '500')) < 5000):
        _tip(
            'Increase HISTFILE/HISTFILESIZE to retain more history',
            indent=ENV_INDENT + 3,
        )
        return True
    return False


def _lint_rename(cmd):
    short_enough = 0.80
    tokens = cmd.split()
    if len(tokens) != 3 or tokens[0] not in {'mv', 'cp'}:
        return False
    prefix, arg1, arg2 = tokens
    match = difflib.SequenceMatcher(a=arg1, b=arg2)\
                   .find_longest_match(0, len(arg1), 0, len(arg2))
    if match.a == 0 and match.b == 0:
        new_cmd = "{}{{{},{}}}".format(
            arg1[match.a:match.a + match.size],
            arg1[match.a + match.size:],
            arg2[match.b + match.size:],
        )
        if float(len(new_cmd)) / len(cmd) <= short_enough:
            print(' '.join(tokens))
            _tip(
                "These args can be shortened: {} {}".format(prefix, new_cmd),
                len(prefix) + 1,
            )
            return True
    return False


def _history_file():
    if len(sys.argv) > 1:
        history_file = sys.argv[1]
    elif os.environ.get('HISTFILE'):
        history_file = os.environ.get('HISTFILE')
    elif _shell() == 'bash':
        history_file = os.path.join(os.path.expanduser('~'), '.bash_history')
    elif _shell() == 'zsh':
        history_file = os.path.join(os.path.expanduser('~'), '.zsh_history')
    elif _shell() == 'csh' or _shell() == 'tcsh':
        history_file = os.path.join(os.path.expanduser('~'), '.history')
    else:
        history_file = os.path.join(os.path.expanduser('~'), '.history')
    if not os.path.isfile(history_file):
        _warn("Your shell '{}' has no history file '{}'.".format(
            os.environ.get('SHELL'), history_file))
        sys.exit(1)
    return history_file


def _shell():
    return os.path.basename(os.environ.get('SHELL'))


def _is_shellcheck_installed():
    return distutils.spawn.find_executable('shellcheck')


def _is_in_histignore(cmd):
    return _standardize(cmd) in os.environ.get('HISTIGNORE', '').split(':')


def _remove_prefix(text, regexp):
    match = re.search("^{}".format(regexp), text)
    if not match or not text.startswith(match.group(0)):
        return text
    return text[len(match.group(0)):]


def _standardize(cmd):
    return ' '.join(cmd.split())


def main():
    """Run all reports."""
    report_environment()
    history_file = _history_file()
    with open(history_file) as stream:
        commands = [cmd.strip() for cmd in stream.readlines() if cmd.strip()]
    report_favorites(commands)
    report_commands_with_arguments(commands)
    report_miscellaneous(commands)
    report_shellcheck(history_file)


if __name__ == '__main__':
    main()