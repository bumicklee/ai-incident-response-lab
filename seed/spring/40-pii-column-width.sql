-- The base shopping-mall app encrypts these fields on new writes. The original
-- mall.sql widths fit plaintext but truncate AES-GCM ciphertext. Match the
-- widths declared in mall-security's PiiReencrypt.COLUMNS.
ALTER TABLE ums_member MODIFY phone VARCHAR(512) NULL COMMENT '휴대폰 번호(암호문)';
ALTER TABLE oms_order MODIFY receiver_phone VARCHAR(512) NOT NULL COMMENT '수령인 전화번호(암호문)';
ALTER TABLE oms_order MODIFY receiver_detail_address VARCHAR(512) NULL COMMENT '상세 주소(암호문)';
ALTER TABLE ums_member_receive_address MODIFY phone_number VARCHAR(512) NULL COMMENT '전화번호(암호문)';
ALTER TABLE ums_member_receive_address MODIFY detail_address VARCHAR(512) NULL COMMENT '상세 주소(암호문)';
