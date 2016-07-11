'use strict';

// Declare classes
var Match = function(singles, date, time, location, players, confirmed) {
	this.singles = singles;
	this.date = date;
	this.time = time;
	this.location = location;
	this.players = players;
	this.confirmed = confirmed;
};


// AngularJS
var app = angular.module('dashboard', ['ngRoute']);

app.config(['$routeProvider', function($routeProvider) {
	$routeProvider
		.when('/' , {templateUrl: '/templates/summary.html', controller: 'SummaryCtrl as summary'})
		.when('/pend_match' , {templateUrl: '/templates/pend_match.html', controller: 'MatchCtrl as match'})
		.otherwise({redirectTo:'/'});
}]);

// "Global variable"/service to store current match to view details of it
app.factory('currentMatch', function() {
	var myMatch = new Match(true, 'a', 'b', 'c', 'd', false);

	function set(match) {
		myMatch = match;
	}

	function get() {
		return myMatch;
	}

	return {
		set: set,
		get: get
	}
});

app.controller('SummaryCtrl', function(currentMatch) {
	var summary = this;
	summary.firstName = '';
	summary.confirmedMatches = [];
	summary.pendingMatches = [];
	summary.availableMatches = [];

	summary.showPendMatch = function(match) {
		currentMatch.set(match);
		window.location.href = '#/pend_match';
	};
});

app.controller('MatchCtrl', function(currentMatch) {
	var match = this;
	match.currentMatch = currentMatch.get();
});


// Any Google API functionality must be executed -after- the gapi is loaded, thus it's placed in a callback
function onGapiLoad() {
	// Check Google OAuth
	gapi.auth.authorize({client_id: CLIENT_ID, scope: SCOPES, immediate: true}, handleAuthResult);
}

function handleAuthResult(authResult) {
	// Get Angular scope
	var $scope = $('#dashboard').scope();

	if (authResult && !authResult.error) {
		// Get user profile, show personalized greeting
		gapi.client.tennis.getProfile().execute(function(resp) {
			var userId = resp.result.userId;

			// If user has not created a profile, redirect to profile page
			// Else, stay here and update greeting
			if (resp.result.firstName == '' || resp.result.lastName == '') {
				window.location.href = '/profile';
			} else {
				$scope.$apply(function () { $scope.summary.firstName = resp.result.firstName; });
			}
		});

		// Get all matches for current user, populate Confirmed Matches and Pending Matches
		gapi.client.tennis.getMyMatches().execute(function(resp) {
			if ($.isEmptyObject(resp.result)) { return; }

			// The MatchesMsg message is stored in resp.result
			// Go through all matches in the match "list" (see models.py for format)
			var matches = resp.result;
			var num_matches = matches.singles.length;

			var confirmedMatches = [];
			var pendingMatches = [];

			for (var i = 0; i < num_matches; i++) {
				var newMatch = new Match(
					matches.singles[i],
					matches.date[i],
					matches.time[i],
					matches.location[i],
					matches.players[i],
					matches.confirmed[i]
				);

				if (newMatch.confirmed) {
					confirmedMatches.push(newMatch);
				} else {
					pendingMatches.push(newMatch);
				}
			}

			// Point to the confirmed/pendingMatches in the controller
			$scope.$apply(function () {
				$scope.summary.confirmedMatches = confirmedMatches;
				$scope.summary.pendingMatches = pendingMatches;
			});
		});

		// Query all available matches for current user, populate Available Matches
		gapi.client.tennis.getAvailableMatches().execute(function(resp) {
			if ($.isEmptyObject(resp.result)) { return; }

			// The MatchesMsg message is stored in resp.result
			// Go through all matches in the match "list" (see models.py for format)
			var matches = resp.result;
			var num_matches = matches.singles.length;

			var availableMatches = [];

			for (var i = 0; i < num_matches; i++) {
				var newMatch = new Match(
					matches.singles[i],
					matches.date[i],
					matches.time[i],
					matches.location[i],
					matches.players[i],
					matches.confirmed[i]
				);

				availableMatches.push(newMatch);
			}

			// Point to the availableMatches in the controller
			$scope.$apply(function () {
				$scope.summary.availableMatches = availableMatches;
			});
		});

	} else {
		// If user is not authorized, redirect to login page
		window.location = '/login';
	}
}

/*
// On-click handlers
$('#req-button').click(function() {
	window.location.href = '/req_match';
});

$('.conf-match').click(function() {
	window.location.href = '/conf_match';
});

$('.pend-match').click(function() {
	window.location.href = '/pend_match';
});

$('.avail-match').click(function() {
	window.location.href = '/avail_match';
});
*/