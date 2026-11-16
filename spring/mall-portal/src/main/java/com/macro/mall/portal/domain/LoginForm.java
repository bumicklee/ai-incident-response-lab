package com.macro.mall.portal.domain;

/**
 * [secure-kit] 로그인 요청 칸. secure-kit의 @RateLimited는 인자 객체의 username 칸에서 계정을 꺼내므로,
 * 낱개 @RequestParam 대신 이 객체로 받는다. 폼 전송(application/x-www-form-urlencoded) 그대로 묶인다.
 */
public record LoginForm(String username, String password) {
}
