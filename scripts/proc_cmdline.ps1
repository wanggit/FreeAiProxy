Get-CimInstance Win32_Process -Filter "Name='camoufox.exe'" |
  Where-Object { $_.CommandLine -notmatch 'contentproc' } |
  ForEach-Object {
    $profile = if ($_.CommandLine -match '-profile (\S+)') { Split-Path $Matches[1] -Leaf } else { '?' }
    $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
    $ppName = if ($pp) { $pp.Name } else { 'gone' }
    "{0}`tprofile={1}`tparent={2}({3})" -f $_.ProcessId, $profile, $_.ParentProcessId, $ppName
  }
"---- python ----"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  ForEach-Object {
    $cl = $_.CommandLine
    if ($cl.Length -gt 100) { $cl = $cl.Substring(0, 100) }
    "{0}`t{1}" -f $_.ProcessId, $cl
  }
