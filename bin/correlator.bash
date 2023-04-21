# usage: source correlator.bash

# use a random function name to avoid name collision
tbxvBOw94EtorcMVC25yX63uDAnvk_IB2H7eLMY0hyI () {
	# get my path
	local bin="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"
	# add to PATH and PYTHONPATH
	export PATH="$bin:$PATH"
	export PYTHONPATH="$(dirname "$bin")/pylib:$PYTHONPATH"
}
# call the function, then remove it from the namespace
tbxvBOw94EtorcMVC25yX63uDAnvk_IB2H7eLMY0hyI
unset -f tbxvBOw94EtorcMVC25yX63uDAnvk_IB2H7eLMY0hyI
