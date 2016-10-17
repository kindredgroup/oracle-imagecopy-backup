-- Sample procedures and queries to automatically monitor autorestore status (from Nagios for example)

FUNCTION autorestore_failed(p_msg OUT VARCHAR2) RETURN NUMBER IS
  p_dbs VARCHAR2(300);
  p_return NUMBER:= 0; -- Return OK
BEGIN
  SELECT listagg(db_unique_name, ', ') within group (order by db_unique_name) INTO p_dbs FROM (
    SELECT db_unique_name, success, start_time, rank() over(partition by db_unique_name order by start_time desc) rnk FROM autorestore.restoreaction WHERE start_time > sysdate-30)
  WHERE rnk = 1 AND success = 0;
  IF p_dbs IS NOT NULL THEN
    p_msg:= 'Databases where the last autorestore failed: '||p_dbs;
    p_return:= 2; -- Return CRITICAL
  END IF;
  RETURN p_return;
END;

FUNCTION autorestore_runtime(p_msg OUT VARCHAR2) RETURN NUMBER IS
  p_cnt NUMBER;
  p_dbs VARCHAR2(300);
  p_return NUMBER:= 0; -- Return OK
BEGIN
  SELECT count(*), listagg(db_unique_name, ', ') within group (order by db_unique_name) INTO p_cnt, p_dbs FROM (
    SELECT DISTINCT db_unique_name FROM autorestore.restoreaction WHERE start_time > sysdate-30
    MINUS
    SELECT db_unique_name FROM autorestore.restoreaction WHERE start_time >= sysdate-2);
  IF p_cnt > 0 THEN
    p_msg:= 'The following databases have not been restored within 2 days: '||p_dbs;
    p_return:= 2; -- Return CRITICAL
  END IF;
  RETURN p_return;
END;
