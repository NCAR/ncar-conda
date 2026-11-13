# NPL: csh/tcsh version of activate.sh. Sets the variables listed in
# $CONDA_PREFIX/etc/npl/env_vars (KEY=VALUE per line, no spaces in values), saving each
# one's prior value as NPL_SAVED_<CONDA_SHLVL>_<KEY> (per nesting level, so `conda activate --stack` works) so deactivate restores it exactly.
if ( -r "${CONDA_PREFIX}/etc/npl/env_vars" ) then
    set _npl_noglob = $?noglob
    set _npl_lvl = 0
    if ( $?CONDA_SHLVL ) set _npl_lvl = $CONDA_SHLVL
    set noglob
    # names are unquoted below: tcsh rejects quoted names in setenv ("must begin with a letter")
    foreach _npl_kv ( `grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${CONDA_PREFIX}/etc/npl/env_vars"` )
        set _npl_k = `echo "$_npl_kv" | sed 's/=.*//'`
        set _npl_v = `echo "$_npl_kv" | sed 's/^[^=]*=//'`
        printenv $_npl_k >& /dev/null
        if ( $status == 0 ) then
            setenv NPL_SAVED_${_npl_lvl}_$_npl_k "`printenv $_npl_k`"
        endif
        setenv $_npl_k "$_npl_v"
    end
    if ( ! $_npl_noglob ) unset noglob
    unset _npl_noglob _npl_lvl _npl_kv _npl_k _npl_v
endif
